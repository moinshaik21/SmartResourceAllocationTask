"""All workflow rules live here so views stay thin."""
import json
import logging
import re
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import AppState, Capture, DailyRoster, Notification, Outbox, Project, Split, Transfer, User
from .sheet import get_book, get_sheet

log = logging.getLogger(__name__)


class WorkflowError(Exception):
    pass


class RowTaken(WorkflowError):
    """Someone already filled the Resource cell of this row by hand, so it is not ours to assign."""


# ---------- helpers ---------------------------------------------------------
def parse_priority(value):
    return {
        "high": 1, "urgent": 1, "p1": 1, "1": 1,
        "medium": 2, "normal": 2, "p2": 2, "2": 2,
        "low": 3, "p3": 3, "3": 3,
    }.get(str(value or "").strip().lower(), 2)


def notify(user, message, capture=None, kind="info"):
    if user:
        Notification.objects.create(user=user, message=message, capture=capture, kind=kind)


def notify_admins(message, capture=None, kind="info"):
    for admin in User.objects.filter(role="admin", is_active=True):
        notify(admin, message, capture, kind)


def work_day(dt=None):
    """The working day of `dt` (default now). Rolls over at DAY_START_HOUR local time,
    so with DAY_START_HOUR=7 a 7pm-6am shift is one working day."""
    return (timezone.localtime(dt) - timedelta(hours=settings.DAY_START_HOUR)).date()


_checked = None
_fail_until = None


def reset_day():
    """Wipe everything that belongs to a working day. Admin accounts are never touched."""
    with transaction.atomic():
        counts = {"projects": Project.objects.count(), "captures": Capture.objects.count(),
                  "resources": 0}
        Notification.objects.all().delete()
        DailyRoster.objects.all().delete()
        Project.objects.all().delete()  # cascades to captures and splits
        if not settings.KEEP_RESOURCE_ACCOUNTS:
            counts["resources"], _ = User.objects.filter(role="resource").delete()
    return counts


def release_unfinished():
    """Clear the Resource cell (and colour) of work nobody finished, so it is 'new' again tomorrow.
    Raises on failure, which aborts the wipe (nothing is lost)."""
    if not settings.CAPTURE_RELEASE_UNFINISHED or "resource" not in settings.CAPTURE_WRITEBACK:
        return 0
    n = 0
    tab = None
    for c in Capture.objects.select_related("project").exclude(status__in=(Capture.COMPLETED, Capture.NEW)).filter(
            started_at__isnull=False, sheet_row__isnull=False):
        tab = tab or get_sheet()
        if c.expanded:
            for sp in c.splits.exclude(status=Split.DONE).filter(sheet_row__isnull=False):
                if sp.named:
                    back = sp.orig_fill if sp.orig_fill not in ("", "none") else "clear"
                    tab.update_row(sp.sheet_row, {"resource": ""}, ident=(c.project.name, sp.row_name, c.ref), color=back)
                    n += 1
        else:
            tab.update_row(c.sheet_row, {"resource": ""}, ident=_ident(c), color="clear")
            n += 1
    return n


def end_shift(day=None):
    """Archive the shift to the workbook FIRST, then wipe. Any error here aborts before the wipe."""
    if day is None:
        st = AppState.objects.filter(pk=1).first()
        day = st.day if st else work_day()
    book = get_book()
    archived = 0
    if book:
        log_pending_splits()
        flush_outbox(book)
        archived = archive_shift(day, book)
        release_unfinished()
        book.tab("Today", TODAY_HEADERS).reset(TODAY_HEADERS)
        clear_daily_columns()
    counts = reset_day()
    counts["archived"] = archived
    return counts


def ensure_today():
    """Start a fresh day when the date has changed: archive to the sheet, then wipe.
    If the archive fails nothing is wiped and it is retried 5 minutes later."""
    global _checked, _fail_until
    day = work_day()
    if _checked == day:
        return False
    if _fail_until and timezone.now() < _fail_until:
        return False
    try:
        with transaction.atomic():
            state, created = AppState.objects.select_for_update().get_or_create(pk=1, defaults={"day": day})
            changed = (not created) and state.day != day
            if changed:
                end_shift(state.day)
                state.day = day
                state.save()
    except Exception:  # noqa: BLE001
        _fail_until = timezone.now() + timedelta(minutes=5)
        log.exception("End-of-shift archive failed - data was NOT wiped, will retry in 5 minutes")
        return False
    _checked = day
    return changed


def parse_people(text):
    """Lines like 'Asha, asha@x.com, pass123' or just 'asha@x.com'. Password optional."""
    out = []
    for line in re.split(r"[\n;]+", text or ""):
        parts = [p.strip() for p in re.split(r"[,\t]+", line) if p.strip()]
        emails = [p for p in parts if "@" in p]
        if not emails:
            continue
        if len(emails) > 1:  # "a@x.com, b@x.com" on one line
            out += [{"name": e.split("@")[0].replace(".", " ").title(), "email": e.lower(), "password": ""} for e in emails]
            continue
        rest = [p for p in parts if p != emails[0]]
        out.append({"name": rest[0] if rest else emails[0].split("@")[0].replace(".", " ").title(),
                    "email": emails[0].lower(), "password": rest[1] if len(rest) > 1 else ""})
    return out


def add_people(people):
    """Create/refresh today's resource accounts. Returns (users, credentials_to_share)."""
    users, creds = [], []
    for p in people:
        u = User.objects.filter(email=p["email"]).first()
        if u and u.role != "resource":
            continue  # never touch admin accounts
        pw = p["password"] if len(p["password"]) >= 6 else ""
        if not u:
            pw = pw or secrets.token_urlsafe(6)
            u = User.objects.create_user(email=p["email"], password=pw, name=p["name"], role="resource")
            try:
                sheet_upsert_user(u)
            except Exception as e:  # noqa: BLE001
                u.delete()
                raise WorkflowError(f"Could not save {p['email']} to the Users tab: {e}")
            creds.append({"name": u.name, "email": u.email, "password": pw})
        else:
            if pw:
                u.set_password(pw)
                creds.append({"name": u.name, "email": u.email, "password": pw})
            if not u.is_active:
                u.is_active = True
            u.save()
            if pw:
                sheet_upsert_user(u)
        users.append(u)
    return users, creds


def roster_users(date=None):
    date = date or work_day()
    return list(User.objects.filter(rosters__date=date, is_active=True, role="resource").order_by("name"))


def open_load(user):
    """How much unfinished work a person is carrying."""
    caps = Capture.objects.filter(owner=user, status__in=Capture.OPEN).count()
    helper = (Split.objects.filter(assignee=user).exclude(status=Split.DONE)
              .exclude(capture__owner=user).count())
    return caps + helper


def _stamp(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M") if dt else ""


def _ident(c):
    return (c.project.name, c.name, c.ref)


def write_back(capture):
    """Push resource, status, splits and times to the capture row. Never breaks the workflow."""
    if not capture.sheet_row or capture.expanded:   # expanded rows are written split by split
        return
    try:
        names = []
        if capture.owner:
            names.append(capture.owner.name)
        for s in capture.splits.select_related("assignee"):
            if s.assignee and s.assignee.name not in names:
                names.append(s.assignee.name)
        vals = {
            "resource": ", ".join(names),
            "status": capture.get_status_display(),
            "splits": capture.total_splits or "",
            "picked at": _stamp(capture.picked_at),
            "completed at": _stamp(capture.completed_at),
        }
        vals = {k: v for k, v in vals.items() if k in settings.CAPTURE_WRITEBACK}
        if settings.NAME_ON_START and not capture.started_at:
            vals.pop("resource", None)          # the name goes into the sheet only after pick AND start
        if not vals:
            return
        row = get_sheet().update_row(capture.sheet_row, vals, ident=_ident(capture))
        if row is None:
            log.warning("Row of %s was not found in the sheet - not written", capture)
        elif row != capture.sheet_row:
            capture.sheet_row = row
            capture.save(update_fields=["sheet_row"])
    except Exception:  # noqa: BLE001
        log.exception("Sheet write-back failed for capture %s", capture.pk)


# ---------- sheet sync ------------------------------------------------------
def sync_from_sheet():
    from .models import Project

    rows = get_sheet().read_rows()
    new = 0
    for r in rows:
        pname, cname = r.get("project", ""), r.get("capture", "")
        if not pname or not cname:
            continue
        if r.get("status", "").strip().lower() == "completed":
            continue  # finished on an earlier day – the sheet keeps that record
        project, _ = Project.objects.get_or_create(name=pname)
        cap, created = Capture.objects.get_or_create(
            project=project, name=cname, ref=r.get("ref", ""),
            defaults={"priority": parse_priority(r.get("priority")), "sheet_row": r["_row"],
                      "capture_path": r.get("path", "")[:500], "captured_on": r.get("date", "")[:40]},
        )
        if not created and cap.sheet_row != r["_row"]:
            cap.sheet_row = r["_row"]
            cap.save(update_fields=["sheet_row"])
        new += created
    if new:
        notify_admins(f"{new} new capture{'s' if new != 1 else ''} found in the sheet", kind="sync")
    return {"rows": len(rows), "new": new}


# ---------- assignment ------------------------------------------------------
def assign_capture(capture, user, by=None):
    """Allocate a capture to a person. The sheet is NOT touched until the person picks and starts."""
    return _assign_capture(capture, user, by)


@transaction.atomic
def _assign_capture(capture, user, by=None):
    capture = Capture.objects.select_for_update().get(pk=capture.pk)
    if capture.status in (Capture.WORKING, Capture.COMPLETED):
        raise WorkflowError("This capture is already in progress or completed.")
    capture.owner = user
    capture.status = Capture.ASSIGNED
    capture.assigned_at = timezone.now()
    capture.picked_at = None
    capture.save()
    notify(user, f"You got a task: {capture}", capture, "task")
    write_back(capture)
    return capture


def distribute(date=None):
    """Spread unassigned captures over today's roster: a person gets only one open capture of a project
    when possible, then the least-loaded person wins."""
    users = roster_users(date)
    if not users:
        raise WorkflowError("Add today's working resources first.")
    load = {u.pk: open_load(u) for u in users}
    have = {u.pk: set() for u in users}
    for owner_id, project_id in Capture.objects.filter(owner__in=users, status__in=Capture.OPEN).values_list("owner_id", "project_id"):
        have[owner_id].add(project_id)
    done = 0
    for cap in Capture.objects.filter(status=Capture.NEW).order_by("priority", "created_at", "id"):
        target = min(users, key=lambda u: (cap.project_id in have[u.pk], load[u.pk], u.name))
        try:
            assign_capture(cap, target)
        except RowTaken:
            continue
        load[target.pk] += 1
        have[target.pk].add(cap.project_id)
        done += 1
    return done


def request_task(user):
    """'I need a task' – overflow splits first, then the best unassigned capture."""
    if open_load(user):
        raise WorkflowError("You still have open work. Finish it first.")
    with transaction.atomic():
        split = (Split.objects.select_for_update()
                 .filter(assignee__isnull=True, capture__status=Capture.WORKING)
                 .exclude(status=Split.DONE)
                 .order_by("capture__priority", "capture__created_at", "number").first())
        if split:
            split.assignee = user
            split.save(update_fields=["assignee"])
            notify_admins(f"{user.name} took split {split.number} of {split.capture}", split.capture)
            write_back(split.capture)
            return {"kind": "split", "id": split.pk}
        for cap in (Capture.objects.select_for_update()
                    .filter(status=Capture.NEW).order_by("priority", "created_at", "id")[:25]):
            try:
                assign_capture(cap, user)
            except RowTaken:
                continue
            notify_admins(f"{user.name} requested a task and got {cap}", cap)
            return {"kind": "capture", "id": cap.pk}
    raise WorkflowError("No tasks are waiting right now. Check again in a bit.")


# ---------- capture life-cycle ---------------------------------------------
def pick_capture(capture, user):
    if capture.owner_id != user.pk or capture.status != Capture.ASSIGNED:
        raise WorkflowError("This capture isn't waiting for you to pick it.")
    capture.status = Capture.PICKED
    capture.picked_at = timezone.now()
    capture.save()
    write_back(capture)
    notify_admins(f"{user.name} picked {capture}", capture)
    return capture


def pick_helpers(exclude, how_many):
    """Pick up to `how_many` helpers (one per chunk of splits), least-loaded first."""
    pool = [u for u in roster_users() if u.pk != exclude.pk]
    load = {u.pk: open_load(u) for u in pool}
    chosen = []
    for _ in range(how_many):
        if not pool:
            chosen.append(None)
            continue
        h = min(pool, key=lambda u: (load[u.pk], u.name))
        load[h.pk] += 1
        chosen.append(h)
    return chosen


def split_row_name(capture, n):
    if settings.SPLIT_ROWS_MODE == "insert":
        return settings.SPLIT_NAME_FMT.format(base=capture.name, n=n)
    return capture.name


def declare_splits(capture, user, total, continue_all=None):
    limit = settings.MAX_SPLITS_PER_PERSON
    capture = Capture.objects.select_related("project", "owner").get(pk=capture.pk)
    if capture.owner_id != user.pk or capture.status != Capture.PICKED:
        raise WorkflowError("Pick the capture before entering splits.")
    if total < 1:
        raise WorkflowError("Enter at least one split.")
    if total > limit and continue_all is None:
        return {"needs_decision": True, "limit": limit, "overflow": total - limit}
    own = total if (total <= limit or continue_all) else limit
    rows = _sheet_begin(capture, user, total, own)      # may raise WorkflowError (row already taken)
    return _declare_splits_tx(capture.pk, user, total, own, rows)


def _sheet_begin(capture, user, total, own):
    """Picked + started: the owner's name goes into the sheet (and the row is split into sp-n rows)."""
    if not capture.sheet_row or "resource" not in settings.CAPTURE_WRITEBACK:
        return None
    try:
        tab = get_sheet()
        if settings.SPLIT_ROWS_MODE == "insert":
            names = [split_row_name(capture, n) for n in range(1, total + 1)]
            row = tab.expand(capture.sheet_row, _ident(capture), names, {nm: user.name for nm in names[:own]})
        else:
            row = tab.update_row(capture.sheet_row, {"resource": user.name}, ident=_ident(capture),
                                 must_be_free=True)
    except Exception:  # noqa: BLE001 - sheet unreachable: carry on, the app keeps working
        log.exception("Could not write to the sheet for %s", capture)
        return None
    if row is None:
        reason = "The row was already filled in Excel by someone else"
        with transaction.atomic():
            capture.status = Capture.TRANSFER
            capture.save(update_fields=["status"])
            Transfer.objects.create(capture=capture, from_user=user, reason=reason)
        notify_admins(f"{capture} was already taken in Excel - it needs a new owner", capture, "alert")
        raise WorkflowError("This row was already taken in Excel, so it was sent to the admin.")
    return row


@transaction.atomic
def _declare_splits_tx(capture_id, user, total, own, row):
    limit = settings.MAX_SPLITS_PER_PERSON
    capture = Capture.objects.select_for_update().get(pk=capture_id)
    overflow_numbers = list(range(own + 1, total + 1))
    chunks = [overflow_numbers[i:i + limit] for i in range(0, len(overflow_numbers), limit)]
    helpers = pick_helpers(user, len(chunks)) if chunks else []

    assignee_for = {n: user for n in range(1, own + 1)}
    for chunk, helper in zip(chunks, helpers):
        for n in chunk:
            assignee_for[n] = helper  # None = parked in the shared queue
    insert = settings.SPLIT_ROWS_MODE == "insert"
    base = row or capture.sheet_row
    Split.objects.bulk_create([Split(
        capture=capture, number=n, assignee=assignee_for.get(n), handed_by=user if n > own else None,
        row_name=split_row_name(capture, n), sheet_row=(base + n - 1 if (insert and base) else base),
        named=bool(row) and n <= own) for n in range(1, total + 1)])

    capture.total_splits = total
    capture.status = Capture.WORKING
    capture.started_at = timezone.now()
    capture.expanded = bool(row) and insert
    if row:
        capture.sheet_row = row
    capture.save()

    handed = {}
    for chunk, helper in zip(chunks, helpers):
        if helper:
            handed.setdefault(helper, []).extend(chunk)
    for helper, nums in handed.items():
        notify(helper, f"You got a task: {len(nums)} split(s) of {capture} (from {user.name})", capture, "task")
    parked = sum(len(c) for c, h in zip(chunks, helpers) if h is None)
    if parked:
        notify_admins(f"{parked} split(s) of {capture} are waiting in the queue – nobody on today's roster is free", capture, "alert")
    notify_admins(f"{user.name} started {capture} with {total} splits"
                  + (f" ({len(overflow_numbers)} handed over)" if overflow_numbers else ""), capture)
    write_back(capture)
    return {"needs_decision": False, "capture_id": capture.pk,
            "kept": own, "handed_over": len(overflow_numbers) - parked, "queued": parked}


def _check_split_owner(split, user):
    if split.assignee_id != user.pk:
        raise WorkflowError("This split isn't assigned to you.")


def _mark_split(split, resource=None, color=None):
    """Write a split's own row: name and/or colour. Best effort (never blocks the employee)."""
    cap = split.capture
    if not split.sheet_row or "resource" not in settings.CAPTURE_WRITEBACK:
        return
    try:
        tab = get_sheet()
        if color and not split.orig_fill and hasattr(tab, "get_fill"):     # remember captureautomate's grey/blue
            orig = tab.get_fill(split.sheet_row)
            if orig:
                split.orig_fill = orig
                Split.objects.filter(pk=split.pk).update(orig_fill=orig)
        row = tab.update_row(split.sheet_row, {"resource": resource} if resource else {},
                             ident=(cap.project.name, split.row_name or cap.name, cap.ref), color=color)
        if row and row != split.sheet_row:
            Split.objects.filter(pk=split.pk).update(sheet_row=row)
            split.sheet_row = row
    except Exception:  # noqa: BLE001
        log.exception("Could not mark the sheet row of split %s", split.pk)


def start_split(split, user):
    _check_split_owner(split, user)
    if split.status != Split.PENDING:
        raise WorkflowError("Split already started.")
    split.status = Split.WORKING
    split.started_at = timezone.now()
    split.save()
    _mark_split(split, resource=None if split.named else user.name, color=settings.COLOR_STARTED)
    if not split.named:
        Split.objects.filter(pk=split.pk).update(named=True)
    return split


@transaction.atomic
def finish_split(split, user):
    _check_split_owner(split, user)
    if split.status == Split.DONE:
        raise WorkflowError("Split already finished.")
    split.started_at = split.started_at or timezone.now()
    split.status = Split.DONE
    split.completed_at = timezone.now()
    split.save()
    cap = split.capture
    if cap.expanded:
        _mark_split(split, resource=None if split.named else user.name, color=settings.COLOR_DONE)
    sheet_set_splits_today(user)
    if not cap.splits.exclude(status=Split.DONE).exists():
        cap.status = Capture.COMPLETED
        cap.completed_at = timezone.now()
        cap.save()
        if not cap.expanded and cap.sheet_row and settings.COLOR_DONE and "resource" in settings.CAPTURE_WRITEBACK:
            _mark_split(split, color=settings.COLOR_DONE)     # single-row mode: whole row turns green
        notify_admins(f"{cap} is completed - waiting for pipeline remarks", cap, "done")
        write_back(cap)
    return split


# ---------- workbook: Users tab, Today tab, monthly work log, shift summary ----------------
CAPTURE_HEADERS = ["Project", "Capture", "Priority", "Resource", "Status", "Splits", "Picked At", "Completed At"]
USER_HEADERS = ["Name", "Email", "New Password", "Password Hash", "Role", "Active", "Working Today", "Splits Today", "Senior For"]
TODAY_HEADERS = ["Date", "Email", "Name"]
MONTH_HEADERS = ["Date", "Resource Name", "Email", "Project", "Capture", "Level", "Split Name", "Pipeline", "Remarks", "Done At"]
SUMMARY_HEADERS = ["Shift Date", "Name", "Email", "Captures Completed", "Splits Done",
                   "Splits Handed Over", "First Pick", "Last Done", "Total Minutes", "Team Total Splits (Shift)", "Status"]
GRID_HEADERS = ["Name", "Email"] + [str(i) for i in range(1, 32)] + ["Total"]
LEVEL = {1: "High", 2: "Medium", 3: "Low"}
_users_synced = 0.0


def _ts(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _users_tab():
    book = get_book()
    return book.tab(settings.USERS_TAB, USER_HEADERS) if book else None


def sheet_upsert_user(user):
    """Write/refresh one account (with its password hash) in the Resources tab."""
    if not settings.USERS_FROM_SHEET:
        return
    tab = _users_tab()
    if not tab:
        return
    role = "senior" if (user.role == "resource" and user.senior_for) else user.role
    values = {"name": user.name, "email": user.email, "new password": "", "password hash": user.password,
              "role": role, "active": "Y" if user.is_active else "N"}
    for r in tab.read_rows():
        if r.get("email", "").lower() == user.email:
            tab.update_row(r["_row"], values)
            return
    tab.append([[user.name, user.email, "", user.password, role, "Y" if user.is_active else "N", "", "", user.senior_for]])
    global _users_synced
    _users_synced = 0.0


def sync_users_from_sheet(force=False):
    """Make the database accounts match the Resources tab (the sheet is the source of truth).
    A password typed into 'New Password' is hashed, stored in 'Password Hash' and the typed text is erased."""
    global _users_synced
    if not settings.USERS_FROM_SHEET or (not force and time.time() - _users_synced < 60):
        return 0
    tab = _users_tab()
    if not tab:
        return 0
    seen, n = set(), 0
    for r in tab.read_rows():
        email = r.get("email", "").strip().lower()
        if "@" not in email:
            continue
        seen.add(email)
        sheet_role = r.get("role", "").strip().lower()
        role = "admin" if sheet_role == "admin" else "resource"
        active = r.get("active", "Y").strip().upper() not in ("N", "NO", "FALSE", "0")
        u, created = User.objects.get_or_create(email=email, defaults={"name": r.get("name") or email.split("@")[0]})
        u.name = r.get("name") or u.name
        u.role, u.is_active = role, active
        u.is_staff = role == "admin"
        u.senior_for = r.get("senior for", "") if (sheet_role == "senior" or r.get("senior for")) else ""
        typed = r.get("new password", "")
        if typed:
            u.set_password(typed)
            tab.update_row(r["_row"], {"password hash": u.password, "new password": ""})
        elif r.get("password hash"):
            u.password = r["password hash"]
        u.save()
        n += 1
    # resources removed from the tab lose access (admins created outside the sheet are left alone)
    User.objects.filter(role="resource").exclude(email__in=seen).update(is_active=False)
    _users_synced = time.time()
    return n


def roster_emails_from_sheet():
    """Everybody with Y in 'Working Today' on the Resources tab."""
    sync_users_from_sheet(force=True)
    tab = _users_tab()
    if not tab:
        return []
    out = []
    for r in tab.read_rows():
        if r.get("working today", "").strip().upper() in ("Y", "YES", "TRUE", "1") and "@" in r.get("email", ""):
            out.append(r["email"].strip().lower())
    return out


def pipelines_list():
    """Names offered in the pipeline prompt (Pipelines tab, column 'Pipeline Name')."""
    book = get_book()
    if not book:
        return []
    try:
        rows = book.tab(settings.PIPELINES_TAB, ["Pipeline Name"]).read_rows()
    except Exception:  # noqa: BLE001
        log.exception("Could not read the Pipelines tab")
        return []
    return [r["pipeline name"] for r in rows if r.get("pipeline name")]


def sheet_set_splits_today(user):
    """Keep the 'Splits Today' column of the Resources tab live."""
    if not settings.USERS_FROM_SHEET:
        return
    try:
        tab = _users_tab()
        for r in tab.read_rows():
            if r.get("email", "").lower() == user.email:
                tab.update_row(r["_row"], {"splits today": Split.objects.filter(assignee=user, status=Split.DONE).count()})
                return
    except Exception:  # noqa: BLE001
        log.exception("Could not update Splits Today for %s", user.email)


def clear_daily_columns():
    """End of shift: empty 'Working Today' and 'Splits Today' so tomorrow starts clean."""
    if not settings.USERS_FROM_SHEET:
        return
    tab = _users_tab()
    m = tab.matrix() if tab else []
    if not m:
        return
    head = [str(h).strip().lower() for h in m[0]]
    idx = [head.index(c) for c in ("working today", "splits today") if c in head]
    for row in m[1:]:
        for i in idx:
            if i < len(row):
                row[i] = ""
    tab.write(1, m)


def write_today_tab(day, users):
    try:
        book = get_book()
        if not book:
            return
        tab = book.tab("Today", TODAY_HEADERS)
        tab.reset(TODAY_HEADERS)
        tab.append([[day.isoformat(), u.email, u.name] for u in users])
    except Exception:  # noqa: BLE001
        log.exception("Could not write the Today tab")


def log_capture_splits(capture, pipeline="", remarks=""):
    """Night log: one row per finished split not logged yet (with the pipeline remark when known)."""
    book = get_book()
    if not book:
        return
    pending = list(capture.splits.select_related("assignee").filter(status=Split.DONE, logged=False))
    for sp in pending:
        u = sp.assignee
        if not u:
            continue
        d = work_day(sp.completed_at)
        row = [d.isoformat(), u.name, u.email, capture.project.name, capture.name,
               LEVEL.get(capture.priority, "Medium"), f"Split {sp.number}", pipeline, remarks, _ts(sp.completed_at)]
        tab = settings.LOG_TAB_PREFIX + d.strftime("%Y-%m")
        try:
            book.tab(tab, MONTH_HEADERS).append([row])
        except Exception:  # noqa: BLE001
            log.exception("Monthly log write failed - queued in outbox")
            Outbox.objects.create(tab=tab, row=json.dumps(row))
        Split.objects.filter(pk=sp.pk).update(logged=True)


def log_pending_splits():
    """Safety net before the wipe: log every finished split nobody wrapped up."""
    for cap in Capture.objects.filter(splits__status=Split.DONE, splits__logged=False).distinct():
        log_capture_splits(cap, pipeline=cap.pipeline or "(not provided)", remarks=cap.remarks)


def flush_outbox(book=None):
    book = book or get_book()
    if not book:
        return
    by_tab = {}
    for o in Outbox.objects.order_by("id"):
        by_tab.setdefault(o.tab, []).append(o)
    for tab, items in by_tab.items():
        book.tab(tab, MONTH_HEADERS).append([json.loads(o.row) for o in items])
        Outbox.objects.filter(id__in=[o.id for o in items]).delete()


def archive_shift(day, book=None):
    """One row per person on duty for the shift -> 'Shift Summary' + a day column in 'Daily Work <month>'.
    Safe to run twice (no duplicates)."""
    book = book or get_book()
    tab = book.tab("Shift Summary", SUMMARY_HEADERS)
    seen = {(r.get("shift date"), r.get("email"), r.get("first pick"), r.get("last done")) for r in tab.read_rows()}
    rows, grid = [], []
    team_total = Split.objects.filter(status=Split.DONE).count()
    on_duty = set(DailyRoster.objects.filter(date=day).values_list("user_id", flat=True))
    for u in User.objects.filter(role="resource"):
        done = list(Split.objects.filter(assignee=u, status=Split.DONE))
        caps = Capture.objects.filter(owner=u, status=Capture.COMPLETED).count()
        handed = Split.objects.filter(handed_by=u).count()
        if not done and not caps and u.pk not in on_duty:
            continue
        first = min((s.started_at or s.completed_at for s in done), default=None)
        last = max((s.completed_at for s in done), default=None)
        mins = round((last - first).total_seconds() / 60, 1) if first and last else 0
        status = "Worked" if done else "On duty - no splits"
        row = [day.isoformat(), u.name, u.email, caps, len(done), handed, _ts(first), _ts(last), mins, team_total, status]
        grid.append((u.name, u.email, len(done)))
        if (row[0], row[2], row[6], row[7]) not in seen:
            rows.append(row)
    tab.append(rows)
    update_daily_grid(day, grid, book)
    return len(rows)


def update_daily_grid(day, entries, book):
    """'Daily Work 2026-10': one row per person, one column per day of the month = splits done that day."""
    if not entries:
        return
    tab = book.tab("Daily Work " + day.strftime("%Y-%m"), GRID_HEADERS)
    m = [list(r) for r in (tab.matrix() or [list(GRID_HEADERS)])]
    head, col = m[0], 2 + day.day - 1
    by_email = {r[1].lower(): r for r in m[1:] if len(r) > 1 and r[1]}
    for name, email, count in entries:
        row = by_email.get(email.lower())
        if row is None:
            row = [name, email]
            m.append(row)
            by_email[email.lower()] = row
        row += [""] * (len(head) - len(row))
        row[col] = count
        row[-1] = sum(int(x) for x in row[2:-1] if str(x).strip().isdigit())
    tab.write(1, [[int(x) if isinstance(x, str) and x.strip().isdigit() else x for x in r] for r in m])


# ---------- transfer to admin -> senior ----------------------------------------------------
def request_transfer(capture, user, reason):
    reason = (reason or "").strip()
    if not reason:
        raise WorkflowError("Please write the reason, so the admin can decide.")
    with transaction.atomic():
        capture = Capture.objects.select_for_update().get(pk=capture.pk)
        if capture.owner_id != user.pk or capture.status not in (Capture.ASSIGNED, Capture.PICKED):
            raise WorkflowError("Only a capture you have not started can be sent back to the admin.")
        capture.status = Capture.TRANSFER
        capture.picked_at = None
        capture.save()
        Transfer.objects.create(capture=capture, from_user=user, reason=reason[:500])
    notify_admins(f"{user.name} cannot take {capture}: {reason[:200]}", capture, "alert")
    return capture


def seniors_for(capture):
    """Seniors whose 'Senior For' list mentions this project (case-insensitive, partial match)."""
    pn = capture.project.name.lower()
    out = []
    for u in User.objects.filter(role="resource", is_active=True).exclude(senior_for=""):
        toks = [t.strip().lower() for t in u.senior_for.split(",") if t.strip()]
        if any(t in pn or pn in t for t in toks):
            out.append(u)
    return out


def resolve_transfer(transfer, user_id, admin):
    target = User.objects.filter(pk=user_id, role="resource", is_active=True).first()
    if not target:
        raise WorkflowError("Choose a person from the list.")
    if transfer.status != "open":
        raise WorkflowError("This transfer was already handled.")
    if target.pk == transfer.from_user_id:
        raise WorkflowError("Choose someone other than the person who sent it back.")
    cap = _assign_capture(transfer.capture, target, admin)
    transfer.status, transfer.to_user, transfer.resolved_at = "done", target, timezone.now()
    transfer.save()
    who = transfer.from_user.name if transfer.from_user else "a colleague"
    notify(target, f"Senior task: {cap} (sent back by {who}) - {transfer.reason[:150]}", cap, "task")
    return cap


# ---------- wrap-up: pipeline remark + sub-links -------------------------------------------
def sublink_names(split, count):
    base = split.row_name or split.capture.name
    if count == 1:
        return [f"{base} link-{split.number}"]
    return [f"{base} link-{split.number}-{i}" for i in range(1, count + 1)]


def wrapup_capture(capture, user, pipeline, remarks, sublinks):
    """After the last split: which pipeline was applied, optional note, and sub-links per split."""
    capture = Capture.objects.select_related("project", "owner").get(pk=capture.pk)
    if capture.owner_id != user.pk or capture.status != Capture.COMPLETED:
        raise WorkflowError("Finish all splits first.")
    if capture.wrapped_up:
        raise WorkflowError("The remarks were already submitted.")
    pipeline = (pipeline or "").strip()
    allowed = pipelines_list()
    if not pipeline:
        raise WorkflowError("Choose the pipeline that was applied to these splits.")
    if allowed and pipeline not in allowed:
        raise WorkflowError("Choose the pipeline from the list.")
    remarks = (remarks or "").strip()[:500]
    counts = {}
    for k, v in (sublinks or {}).items():
        try:
            k, v = int(k), int(v or 0)
        except (TypeError, ValueError):
            raise WorkflowError("Sub-link counts must be whole numbers.")
        if v < 0 or v > 50:
            raise WorkflowError("Sub-links per split must be between 0 and 50.")
        if v:
            counts[k] = v
    text = pipeline + (f" - {remarks}" if remarks else "")
    done_before = set(json.loads(capture.sublinks_json or "{}").get("done", []))
    if capture.sheet_row and settings.SPLIT_ROWS_MODE == "insert" and capture.expanded:
        tab = get_sheet()
        try:
            for sp in capture.splits.order_by("number"):                   # remarks on every split row
                if sp.sheet_row:
                    tab.update_row(sp.sheet_row, {"remarks": text}, ident=(capture.project.name, sp.row_name, capture.ref))
            for sp in capture.splits.order_by("-number"):                  # bottom-up: earlier rows do not move
                n = counts.get(sp.number)
                if not n or sp.number in done_before or not sp.sheet_row:
                    continue
                tab.add_rows(sp.sheet_row, (capture.project.name, sp.row_name, capture.ref), sublink_names(sp, n),
                             resource=sp.assignee.name if sp.assignee else "", color=settings.COLOR_DONE,
                             values={"remarks": text})
                done_before.add(sp.number)
        except Exception as e:  # noqa: BLE001
            log.exception("Wrap-up could not be written to Excel")
            capture.sublinks_json = json.dumps({"counts": counts, "done": sorted(done_before)})
            capture.save(update_fields=["sublinks_json"])
            raise WorkflowError(f"Excel could not be updated ({e}). Nothing was lost - press Submit again.")
    elif capture.sheet_row and not capture.expanded:
        try:
            get_sheet().update_row(capture.sheet_row, {"remarks": text}, ident=_ident(capture))
        except Exception:  # noqa: BLE001
            log.exception("Could not write the remark")
    capture.pipeline, capture.remarks, capture.wrapped_up = pipeline, remarks, True
    capture.sublinks_json = json.dumps({"counts": counts, "done": sorted(done_before)})
    capture.save()
    log_capture_splits(capture, pipeline, remarks)
    extra = f", {sum(counts.values())} sub-link row(s) added" if counts else ""
    notify_admins(f"{user.name} finished {capture}: pipeline {pipeline}{extra}", capture, "done")
    return capture


def set_roster(emails, extra_users=()):
    """Today's people (from the roster page or the Working Today column). Returns (users, unknown emails)."""
    users = {u.email: u for u in User.objects.filter(email__in=emails, role="resource", is_active=True)}
    users.update({u.email: u for u in extra_users})
    unknown = [e for e in emails if e not in users]
    day = work_day()
    DailyRoster.objects.filter(date=day).exclude(user__in=users.values()).delete()
    for u in users.values():
        DailyRoster.objects.get_or_create(date=day, user=u)
    write_today_tab(day, list(users.values()))
    return users, unknown


def get_state():
    return AppState.objects.get_or_create(pk=1, defaults={"day": work_day()})[0]


# ---------- Microsoft connection + automatic pulling of new captures ----------------------------------
def after_connect():
    """Called once the admin has signed in: create the Control tabs, list admins, read the Resources tab."""
    book = get_book()
    if not book:
        return
    for name, headers in [(settings.USERS_TAB, USER_HEADERS), (settings.PIPELINES_TAB, ["Pipeline Name"]),
                          ("Today", TODAY_HEADERS), ("Shift Summary", SUMMARY_HEADERS)]:
        book.tab(name, headers)
    for admin in User.objects.filter(role="admin"):
        sheet_upsert_user(admin)
    sync_users_from_sheet(force=True)


def maybe_autosync(force=False):
    """Pull new captures from the sheet at most every AUTO_SYNC_SECONDS - called while the admin dashboard is open.
    Needs the start row typed by the admin, so old rows are never imported by accident."""
    from .sheet import excel_enabled
    st = get_state()
    if not excel_enabled() or not st.start_row or not (force or settings.AUTO_SYNC_SECONDS):
        return None
    now = timezone.now()
    if not force and st.last_sync_at and (now - st.last_sync_at).total_seconds() < settings.AUTO_SYNC_SECONDS:
        return None
    st.last_sync_at = now                                   # claim the slot first: parallel requests skip it
    st.save(update_fields=["last_sync_at"])
    new, error = 0, ""
    try:
        new = sync_from_sheet()["new"]
        if new and st.auto_allocate:
            try:
                distribute()
            except WorkflowError:
                pass
    except Exception as e:  # noqa: BLE001
        log.warning("Auto sync failed: %s", e)
        error = str(e)[:290]
    st.last_sync_new, st.last_sync_error = new, error
    st.save(update_fields=["last_sync_new", "last_sync_error"])
    return {"new": new, "error": error}
