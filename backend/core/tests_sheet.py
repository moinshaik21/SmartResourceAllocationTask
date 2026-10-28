"""Tests: Resources tab, night log + wrap-up, shift archive, transfers, allocation and the Excel (Graph) layout."""
import re
from datetime import timedelta
from unittest import mock
from urllib.parse import unquote

from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import services, sheet
from .models import AppState, Capture, DailyRoster, Outbox, Split, Transfer, User
from .sheet import CsvTab
from .tests import BaseSetup


def urow(name, email, pw="", role="resource", active="Y", working="", senior=""):
    """A row of the Resources tab (Name, Email, New Password, Password Hash, Role, Active, Working Today, Splits Today, Senior For)."""
    return [name, email, "", make_password(pw) if pw else "", role, active, working, "", senior]


def wrapup(client, cap_id, pipeline="Pipe A", remarks="", sublinks=None):
    return client.post(f"/api/captures/{cap_id}/wrapup/", {"pipeline": pipeline, "remarks": remarks,
                                                           "sublinks": sublinks or {}}, format="json")


class ShiftLogTest(BaseSetup):
    def _work_one_capture(self, splits=2):
        adm = self.api(self.admin)
        adm.post("/api/sheet/sync/")
        DailyRoster.objects.create(date=services.work_day(), user=self.u["asha"])
        adm.post("/api/distribute/")
        asha = self.api(self.u["asha"])
        cap = asha.get("/api/me/work/").data["captures"][0]
        asha.post(f"/api/captures/{cap['id']}/pick/")
        asha.post(f"/api/captures/{cap['id']}/splits/", {"total": splits}, format="json")
        return asha, cap

    def _finish_all(self, asha, cap):
        for s in Split.objects.filter(capture_id=cap["id"]):
            asha.post(f"/api/splits/{s.id}/start/"); asha.post(f"/api/splits/{s.id}/done/")

    def _month(self): return services.work_day().strftime("%Y-%m")

    def test_night_log_rows_come_with_the_wrapup(self):
        asha, cap = self._work_one_capture()
        self._finish_all(asha, cap)
        self.assertEqual(len(services.get_book().tab(self._month()).read_rows()), 0)   # waits for the remarks
        r = wrapup(asha, cap["id"], "Pipe A", "all good")
        self.assertEqual(r.status_code, 200, r.data)
        rows = services.get_book().tab(self._month()).read_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["resource name"], rows[0]["level"], rows[0]["split name"], rows[0]["pipeline"], rows[0]["remarks"]),
                         ("Asha", "High", "Split 1", "Pipe A", "all good"))
        self.assertEqual(wrapup(asha, cap["id"]).status_code, 409)                      # only once

    def test_wrapup_needs_pipeline_and_finished_splits(self):
        asha, cap = self._work_one_capture()
        self.assertEqual(wrapup(asha, cap["id"]).status_code, 409)                      # splits not done yet
        self._finish_all(asha, cap)
        self.assertEqual(wrapup(asha, cap["id"], pipeline="").status_code, 409)         # pipeline is required
        services.get_book().tab("Pipelines", ["Pipeline Name"]).append([["Pipe A"], ["Pipe B"]])
        self.assertEqual(wrapup(asha, cap["id"], pipeline="Other").status_code, 409)    # must be one of the list
        self.assertEqual(wrapup(asha, cap["id"], pipeline="Pipe B").status_code, 200)

    def test_shift_close_logs_unwrapped_work_archives_then_wipes(self):
        asha, cap = self._work_one_capture()
        self._finish_all(asha, cap)                                                     # nobody wrapped up
        r = self.api(self.admin).post("/api/reset-day/")
        self.assertEqual((r.status_code, r.data["archived"]), (200, 1))
        night = services.get_book().tab(self._month()).read_rows()
        self.assertEqual((len(night), night[0]["pipeline"]), (2, "(not provided)"))     # nothing lost
        summ = services.get_book().tab("Shift Summary").read_rows()
        self.assertEqual((summ[0]["name"], summ[0]["splits done"], summ[0]["captures completed"]), ("Asha", "2", "1"))
        self.assertEqual(Capture.objects.count(), 0)
        self.assertEqual(services.archive_shift(services.work_day()), 0)               # idempotent

    def test_nothing_is_wiped_if_archive_fails(self):
        asha, cap = self._work_one_capture()
        AppState.objects.update_or_create(pk=1, defaults={"day": services.work_day() - timedelta(days=1)})
        services._checked = services._fail_until = None
        with mock.patch.object(services, "archive_shift", side_effect=RuntimeError("excel down")):
            self.assertEqual(self.api(self.admin).get("/api/dashboard/").status_code, 200)
            self.assertEqual(self.api(self.admin).post("/api/reset-day/").status_code, 502)
        self.assertEqual(Capture.objects.count(), 3)
        services._fail_until = None
        self.api(self.admin).get("/api/dashboard/")
        self.assertEqual(Capture.objects.count(), 0)

    def test_failed_log_goes_to_outbox_and_is_retried(self):
        asha, cap = self._work_one_capture(splits=1)
        self._finish_all(asha, cap)
        with mock.patch.object(CsvTab, "append", side_effect=RuntimeError("quota")):
            wrapup(asha, cap["id"])
        self.assertEqual(Outbox.objects.count(), 1)
        services.flush_outbox()
        self.assertEqual((Outbox.objects.count(), len(services.get_book().tab(self._month()).read_rows())), (0, 1))

    def test_handed_over_count_is_archived(self):
        adm = self.api(self.admin); adm.post("/api/sheet/sync/")
        for n in ("asha", "ravi"):
            DailyRoster.objects.create(date=services.work_day(), user=self.u[n])
        adm.post("/api/distribute/")
        asha = self.api(self.u["asha"]); cap = asha.get("/api/me/work/").data["captures"][0]
        asha.post(f"/api/captures/{cap['id']}/pick/")
        asha.post(f"/api/captures/{cap['id']}/splits/", {"total": 8, "continue_all": False}, format="json")
        self.assertEqual(Split.objects.filter(handed_by=self.u["asha"]).count(), 3)


class AllocationAndTransferTest(BaseSetup):
    def setUp(self):
        super().setUp()
        for n in ("asha", "ravi"):
            DailyRoster.objects.create(date=services.work_day(), user=self.u[n])

    def test_one_capture_per_person_per_project(self):
        from .models import Project
        for pn, names in (("PA", ["A1", "A2"]), ("PB", ["B1", "B2"])):
            p, _ = Project.objects.get_or_create(name=pn)
            for n in names: Capture.objects.create(project=p, name=n)
        services.distribute()
        for u in (self.u["asha"], self.u["ravi"]):
            projects = list(Capture.objects.filter(owner=u).values_list("project__name", flat=True))
            self.assertEqual(sorted(projects), ["PA", "PB"])           # one level of each project, not two of one

    def test_transfer_to_admin_then_to_senior(self):
        self.api(self.admin).post("/api/sheet/sync/"); self.api(self.admin).post("/api/distribute/")
        ravi = self.u["ravi"]; ravi.senior_for = "P1"; ravi.save()
        asha_api = self.api(self.u["asha"])
        cap = asha_api.get("/api/me/work/").data["captures"][0]
        self.assertEqual(asha_api.post(f"/api/captures/{cap['id']}/transfer/", {"reason": ""}, format="json").status_code, 409)
        r = asha_api.post(f"/api/captures/{cap['id']}/transfer/", {"reason": "Not trained on this project"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Capture.objects.get(pk=cap["id"]).status, "with-admin" if False else "transfer")
        self.assertEqual(asha_api.get("/api/me/work/").data["captures"], [c for c in asha_api.get("/api/me/work/").data["captures"] if c["id"] != cap["id"]])
        adm = self.api(self.admin)
        inbox = adm.get("/api/transfers/").data
        self.assertEqual((len(inbox), inbox[0]["reason"], [s["name"] for s in inbox[0]["seniors"]]), (1, "Not trained on this project", ["Ravi"]))
        self.assertEqual(adm.post(f"/api/transfers/{inbox[0]['id']}/resolve/", {"user_id": self.u["asha"].id}, format="json").status_code, 409)
        self.assertEqual(adm.post(f"/api/transfers/{inbox[0]['id']}/resolve/", {"user_id": ravi.id}, format="json").status_code, 200)
        c = Capture.objects.get(pk=cap["id"])
        self.assertEqual((c.owner_id, c.status), (ravi.id, "assigned"))
        self.assertEqual(adm.get("/api/transfers/").data, [])

    def test_a_started_capture_cannot_be_transferred(self):
        self.api(self.admin).post("/api/sheet/sync/"); self.api(self.admin).post("/api/distribute/")
        api = self.api(self.u["asha"]); cap = api.get("/api/me/work/").data["captures"][0]
        api.post(f"/api/captures/{cap['id']}/pick/")
        api.post(f"/api/captures/{cap['id']}/splits/", {"total": 1}, format="json")
        r = api.post(f"/api/captures/{cap['id']}/transfer/", {"reason": "too hard"}, format="json")
        self.assertEqual(r.status_code, 409)


class ResourcesTabTest(BaseSetup):
    def setUp(self):
        super().setUp()
        self.o2 = override_settings(USERS_FROM_SHEET=True); self.o2.enable()
        services._users_synced = 0.0
        self.tab = services.get_book().tab(settings_users(), services.USER_HEADERS)
        self.tab.append([urow("Admin", "a@x.com", "adminpw1", "admin"), urow("Tina", "tina@x.com", "tinapw12", working="Y"),
                         urow("Gone", "gone@x.com", "gonepw12", active="N"),
                         urow("Sam", "sam@x.com", "sampw123", role="senior", working="Y", senior="200 - NIH, Brasfield")])
        self.adm = self.api(User.objects.get_or_create(email="a@x.com", defaults={"name": "Admin", "role": "admin"})[0])

    def tearDown(self):
        self.o2.disable(); super().tearDown()

    def login(self, e, p): return APIClient().post("/api/auth/login/", {"email": e, "password": p}, format="json")

    def test_login_comes_from_the_resources_tab(self):
        self.assertIn("token", self.login("tina@x.com", "tinapw12").data)
        self.assertEqual(self.login("tina@x.com", "wrong").status_code, 401)
        self.assertEqual(self.login("gone@x.com", "gonepw12").status_code, 401)      # Active = N
        self.assertEqual(self.login("asha@x.com", "pw123456").status_code, 401)      # not in the tab
        sam = User.objects.get(email="sam@x.com")
        self.assertEqual((sam.role, sam.senior_for), ("resource", "200 - NIH, Brasfield"))

    def test_typed_password_is_hashed_and_erased(self):
        self.tab.append([["Newbie", "new@x.com", "firstpass1", "", "resource", "Y", "", "", ""]])
        self.assertIn("token", self.login("new@x.com", "firstpass1").data)
        row = [r for r in self.tab.read_rows() if r["email"] == "new@x.com"][0]
        self.assertEqual(row["new password"], "")
        self.assertTrue(row["password hash"].startswith(("md5$", "pbkdf2")) or "$" in row["password hash"])
        self.assertNotIn("firstpass1", str(self.tab.matrix()))

    def test_working_today_column_sets_the_roster(self):
        r = self.adm.post("/api/roster/fetch/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(sorted(u["email"] for u in r.data["on_duty"]), ["sam@x.com", "tina@x.com"])
        self.assertEqual(sorted(x["email"] for x in services.get_book().tab("Today").read_rows()), ["sam@x.com", "tina@x.com"])

    def test_roster_only_accepts_people_in_the_tab(self):
        r = self.adm.post("/api/roster/", {"emails": ["tina@x.com", "ghost@x.com"]}, format="json")
        self.assertEqual(r.data["unknown"], ["ghost@x.com"])

    def test_created_accounts_are_saved_and_can_log_in(self):
        r = self.adm.post("/api/resources/", {"name": "Neha", "email": "neha@x.com", "password": "nehapw12", "senior_for": "P1"}, format="json")
        self.assertEqual(r.status_code, 201)
        row = [x for x in self.tab.read_rows() if x["email"] == "neha@x.com"][0]
        self.assertEqual((row["role"], row["senior for"]), ("senior", "P1"))
        self.assertIn("token", self.login("neha@x.com", "nehapw12").data)
        self.assertNotIn("nehapw12", str(self.tab.matrix()))

    def test_splits_today_is_written_and_daily_columns_cleared_at_shift_end(self):
        self.adm.post("/api/sheet/sync/"); self.adm.post("/api/roster/fetch/")
        tina_api = APIClient()
        tok = self.login("tina@x.com", "tinapw12").data["token"]; tina_api.credentials(HTTP_AUTHORIZATION="Token " + tok)
        self.adm.post("/api/distribute/")
        cap = tina_api.get("/api/me/work/").data["captures"][0]
        tina_api.post(f"/api/captures/{cap['id']}/pick/")
        tina_api.post(f"/api/captures/{cap['id']}/splits/", {"total": 2}, format="json")
        for s in Split.objects.filter(capture_id=cap["id"]):
            tina_api.post(f"/api/splits/{s.id}/done/")
        row = [x for x in self.tab.read_rows() if x["email"] == "tina@x.com"][0]
        self.assertEqual((row["working today"], row["splits today"]), ("Y", "2"))
        self.adm.post("/api/reset-day/")
        row = [x for x in self.tab.read_rows() if x["email"] == "tina@x.com"][0]
        self.assertEqual((row["working today"], row["splits today"], row["name"]), ("", "", "Tina"))   # cleared, person kept


def settings_users():
    from django.conf import settings
    return settings.USERS_TAB


# =====================================================================================================
class FakeGraph:
    """The bits of the Excel API we use: ranges, used range, insert rows, fill colours, Excel's date auto-conversion."""
    def __init__(self): self.ws = {}; self.calls = []
    @staticmethod
    def _n(col):
        n = 0
        for ch in col: n = n * 26 + ord(ch) - 64
        return n
    def _shift(self, w, at, n):
        for key in ("v", "f"):
            w[key] = {((r + n if r >= at else r), c): v for (r, c), v in w[key].items()}
        w["fill"] = {(r + n if r >= at else r): v for r, v in w.setdefault("fill", {}).items()}
    def insert_row(self, name, at): self._shift(self.ws[name], at, 1)
    def __call__(self, method, url, body=None):
        url, _, query = url.partition("?")
        self.calls.append((method, url, body))
        url = url.replace("/ITEM/workbook/", "")
        if url == "worksheets/add":
            self.ws[body["name"]] = {"v": {}, "f": {}, "fill": {}}; return {}
        m = re.match(r"worksheets/([^/]+)(?:/(.*))?$", url)
        name, rest = unquote(m.group(1)), m.group(2)
        if name not in self.ws: raise sheet.GraphError(404, "ItemNotFound")
        w = self.ws[name]; w.setdefault("fill", {})
        if rest is None: return {"name": name}
        if rest.startswith("usedRange(valuesOnly"):
            if not w["v"]: return {"address": f"{name}!A1", "values": [[""]]}
            R, C = max(r for r, _ in w["v"]), max(c for _, c in w["v"])
            if "address" in query: return {"address": f"{name}!A1:{sheet._col(C)}{R}"}
            return {"values": [[w["v"].get((r, c), "") for c in range(1, C + 1)] for r in range(1, R + 1)]}
        if rest == "usedRange/clear": w["v"].clear(); w["f"].clear(); w["fill"].clear(); return {}
        a = re.match(r"range\(address='([^']+)'\)(/.*)?$", rest)
        addr, tail = a.group(1), a.group(2)
        if re.fullmatch(r"\d+:\d+", addr):                                   # whole rows
            r1, r2 = (int(x) for x in addr.split(":"))
            assert tail == "/insert", tail
            self._shift(w, r1, r2 - r1 + 1); return {}
        c = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", addr)
        c1, r1 = self._n(c.group(1)), int(c.group(2))
        c2, r2 = (self._n(c.group(3)), int(c.group(4))) if c.group(3) else (c1, r1)
        if tail:
            assert tail.startswith("/format/fill")
            for r in range(r1, r2 + 1):
                if tail.endswith("/clear"): w["fill"].pop(r, None)
                else: w["fill"][r] = body["color"]
            return {}
        if method == "GET":
            return {"values": [[w["v"].get((r, cc), "") for cc in range(c1, c2 + 1)] for r in range(r1, r2 + 1)]}
        for i, row in enumerate(body.get("numberFormat", [])):
            for j, f in enumerate(row): w["f"][(r1 + i, c1 + j)] = f
        for i, row in enumerate(body.get("values", [])):
            for j, v in enumerate(row):
                if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) and w["f"].get((r1 + i, c1 + j)) != "@":
                    v = 46000
                if v == "": w["v"].pop((r1 + i, c1 + j), None)
                else: w["v"][(r1 + i, c1 + j)] = v
        return {}


MAIN = "Detail Visual projects"
COLORS = dict(COLOR_STARTED="#FFFF00", COLOR_DONE="#00B050")


@override_settings(GRAPH_TENANT_ID="t", GRAPH_CLIENT_ID="c", GRAPH_CLIENT_SECRET="s", GRAPH_FILE_URL="https://x/control.xlsx",
                   GRAPH_CAPTURES_URL="https://x/production.xlsx", GOOGLE_SHEET_ID="", USERS_FROM_SHEET=True,
                   CAPTURES_TAB=MAIN, CAPTURE_COL_PROJECT="Projects", CAPTURE_COL_CAPTURE="Structure",
                   CAPTURE_COL_REF="JOB ID", CAPTURE_COL_PRIORITY="", CAPTURE_COL_STATUS="", CAPTURE_COL_REMARKS="Remarks",
                   CAPTURE_LOOKBACK_ROWS=15, CAPTURE_WRITEBACK=["resource"], CAPTURE_RELEASE_UNFINISHED=True,
                   LOG_TAB_PREFIX="Night ", SPLIT_ROWS_MODE="insert", NAME_ON_START=True, **COLORS)
class RealSheetTest(TestCase):
    """Your real layout: A=notes, B=JOB ID, C=Resource, D=Projects, E=Structure, F=Remarks; thousands of old rows."""
    def setUp(self):
        sheet.reset_cache(); services._checked = services._fail_until = None; services._users_synced = 0.0
        self.fake = FakeGraph()
        for p in (mock.patch.object(sheet, "_call", self.fake), mock.patch.object(sheet, "_item", lambda url=None: "/ITEM")):
            p.start(); self.addCleanup(p.stop)
        v = {}
        for c, h in enumerate(["", "JOB ID", "Resource", "Projects", "Structure", "Remarks"], 1):
            if h: v[(1, c)] = h
        def put(r, note, job, res, proj, st):
            for c, x in enumerate([note, job, res, proj, st], 1):
                if x: v[(r, c)] = x
        for r in range(2, 7): put(r, "", "OLD", "", "Old project", f"old-{r}")
        for r in range(7, 22): put(r, "done", "OLD", "someone", "Old project", f"old-{r}")
        put(22, "", "JOB1", "", "30760 - Brasfield", "Level 05")
        put(23, "", "JOB1", "", "30760 - Brasfield", "Level 06")
        put(24, "", "JOB2", "", "200 - NIH SRLM", "Level 09")
        put(26, "V10 Auto tm worked in reglab", "JOB3", "vara", "201 - NIH", "Level 09 sp-2")
        self.fake.ws[MAIN] = {"v": v, "f": {}, "fill": {}}
        book = sheet.get_book()
        book.tab(services.settings.USERS_TAB, services.USER_HEADERS).append([
            urow("Boss", "boss@x.com", "bosspw12", "admin"),
            urow("Asha", "asha@x.com", "ashapw12", working="Y"),
            urow("Ravi", "ravi@x.com", "ravipw12", working="Y", role="senior", senior="NIH")])
        book.tab("Pipelines", ["Pipeline Name"]).append([["Pipe A"], ["Pipe B"]])
        self.cl = {}
        for e in ("boss", "asha", "ravi"):
            r = APIClient().post("/api/auth/login/", {"email": f"{e}@x.com", "password": e + "pw12"}, format="json")
            c = APIClient(); c.credentials(HTTP_AUTHORIZATION="Token " + r.data["token"]); self.cl[e] = c

    # helpers
    def cell(self, r, c): return self.fake.ws[MAIN]["v"].get((r, c), "")
    def fill(self, r): return self.fake.ws[MAIN]["fill"].get(r)
    def find_row(self, text, col=5):
        return [r for (r, c), v in self.fake.ws[MAIN]["v"].items() if c == col and v == text]
    def start_shift(self, mode="auto", start=22):
        return self.cl["boss"].post("/api/shift/start/", {"start_row": start, "mode": mode}, format="json")
    def mine(self, who): return self.cl[who].get("/api/me/work/").data["captures"]
    def begin(self, who, name, total, keep_all=True):
        cap = next(c for c in self.mine(who) if c["name"] == name)
        self.cl[who].post(f"/api/captures/{cap['id']}/pick/")
        r = self.cl[who].post(f"/api/captures/{cap['id']}/splits/", {"total": total, "continue_all": keep_all}, format="json")
        return cap["id"], r

    def test_start_shift_reads_from_the_typed_row_and_fetches_resources(self):
        r = self.start_shift(mode="manual", start=22)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["sync"]["new"], 3)                       # not the 5 old empty rows above row 22
        self.assertEqual(sorted(u["email"] for u in r.data["on_duty"]), ["asha@x.com", "ravi@x.com"])
        self.assertEqual(sorted(Capture.objects.values_list("sheet_row", flat=True)), [22, 23, 24])
        self.assertEqual(self.cl["boss"].get("/api/sheet/config/").data["start_row"], 22)
        self.assertEqual(self.start_shift(start=1).status_code, 400)      # row 1 is the header

    def test_allocation_does_not_touch_excel(self):
        self.start_shift(mode="auto")
        self.assertEqual(Capture.objects.filter(status="assigned").count(), 3)
        self.assertEqual([self.cell(r, 3) for r in (22, 23, 24)], ["", "", ""])
        self.assertFalse([c for c in self.fake.calls if c[0] in ("PATCH", "POST") and "production" in str(c)])
        self.assertEqual(self.fake.ws[MAIN]["fill"], {})

    def test_pick_and_start_writes_name_and_splits_the_row(self):
        self.start_shift(mode="auto")
        who = "asha" if Capture.objects.get(name="Level 05").owner.email == "asha@x.com" else "ravi"
        who_name = who.capitalize()
        _, r = self.begin(who, "Level 05", 3)
        self.assertEqual(r.status_code, 200, r.data)
        rows = sorted(self.find_row("Level 05 sp-1") + self.find_row("Level 05 sp-2") + self.find_row("Level 05 sp-3"))
        self.assertEqual(rows, [22, 23, 24])                              # one row per split, right under each other
        self.assertTrue(all(self.cell(x, 3) == who_name for x in rows))
        self.assertTrue(all(self.cell(x, 2) == "JOB1" and self.cell(x, 4) == "30760 - Brasfield" for x in rows))
        self.assertEqual(self.find_row("Level 06"), [25])                  # the next Level moved down, untouched
        self.assertEqual(self.cell(28, 3), "vara")                         # old rows moved down by 2, still intact
        self.assertEqual(self.cell(28, 1), "V10 Auto tm worked in reglab")

    def test_colours_yellow_when_started_green_when_done(self):
        self.start_shift(mode="auto")
        owner = Capture.objects.get(name="Level 05").owner.email.split("@")[0]
        cid, _ = self.begin(owner, "Level 05", 2)
        s1, s2 = Split.objects.filter(capture_id=cid).order_by("number")
        self.assertIsNone(self.fill(22))                                   # nothing coloured before Start
        self.cl[owner].post(f"/api/splits/{s1.id}/start/")
        self.assertEqual(self.fill(22), "#FFFF00")
        self.cl[owner].post(f"/api/splits/{s1.id}/done/")
        self.assertEqual(self.fill(22), "#00B050")
        self.assertIsNone(self.fill(23))                                   # split 2 not started yet
        self.cl[owner].post(f"/api/splits/{s2.id}/start/"); self.assertEqual(self.fill(23), "#FFFF00")

    def test_handed_over_rows_get_a_name_only_when_the_helper_starts(self):
        self.start_shift(mode="auto")
        owner = Capture.objects.get(name="Level 05").owner.email.split("@")[0]
        helper = "ravi" if owner == "asha" else "asha"
        cid, r = self.begin(owner, "Level 05", 7, keep_all=False)         # 5 stay, 2 are handed over
        self.assertEqual(r.status_code, 200, r.data)
        handed = list(Split.objects.filter(capture_id=cid, handed_by__isnull=False).order_by("number"))
        self.assertEqual([(x.number, x.assignee.email.split("@")[0]) for x in handed], [(6, helper), (7, helper)])
        r6, r7 = self.find_row("Level 05 sp-6")[0], self.find_row("Level 05 sp-7")[0]
        self.assertEqual((self.cell(r6, 3), self.cell(r7, 3)), ("", ""))     # rows exist, nobody's name yet
        self.assertEqual(self.cell(self.find_row("Level 05 sp-5")[0], 3), owner.capitalize())
        self.cl[helper].post(f"/api/splits/{handed[0].id}/start/")          # the helper starts split 6
        self.assertEqual((self.cell(r6, 3), self.fill(r6)), (helper.capitalize(), "#FFFF00"))
        self.assertEqual(self.cell(r7, 3), "")                               # split 7 not started: still empty

    def test_row_already_filled_by_hand_goes_to_the_admin(self):
        self.start_shift(mode="auto")
        owner = Capture.objects.get(name="Level 05").owner.email.split("@")[0]
        self.fake.ws[MAIN]["v"][(22, 3)] = "Human"                         # a colleague typed a name meanwhile
        cap = next(c for c in self.mine(owner) if c["name"] == "Level 05")
        self.cl[owner].post(f"/api/captures/{cap['id']}/pick/")
        r = self.cl[owner].post(f"/api/captures/{cap['id']}/splits/", {"total": 2}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(Capture.objects.get(pk=cap["id"]).status, "transfer")
        self.assertEqual(Transfer.objects.filter(status="open").count(), 1)
        self.assertEqual(self.cell(22, 3), "Human"); self.assertEqual(self.cell(22, 5), "Level 05")   # untouched

    def test_rows_that_moved_are_found_again(self):
        self.start_shift(mode="auto")
        self.fake.insert_row(MAIN, 10)                                     # someone inserted a row above
        owner = Capture.objects.get(name="Level 05").owner.email.split("@")[0]
        self.begin(owner, "Level 05", 2)
        self.assertEqual(sorted(self.find_row("Level 05 sp-1") + self.find_row("Level 05 sp-2")), [23, 24])
        self.assertEqual(self.cell(23, 3), owner.capitalize())

    def test_wrapup_writes_remarks_and_sublink_rows_in_green(self):
        self.start_shift(mode="auto")
        owner = Capture.objects.get(name="Level 05").owner.email.split("@")[0]
        cid, _ = self.begin(owner, "Level 05", 2)
        for s in Split.objects.filter(capture_id=cid):
            self.cl[owner].post(f"/api/splits/{s.id}/start/"); self.cl[owner].post(f"/api/splits/{s.id}/done/")
        self.assertIn("Pipe A", self.cl[owner].get("/api/pipelines/").data["pipelines"])
        self.assertEqual(self.cl[owner].get("/api/me/work/").data["wrapup_pending"][0]["id"], cid)
        r = wrapup(self.cl[owner], cid, "Pipe A", "checked twice", {"1": 2, "2": 1})
        self.assertEqual(r.status_code, 200, r.data)
        names = [self.cell(x, 5) for x in range(22, 28)]
        self.assertEqual(names[:5], ["Level 05 sp-1", "Level 05 sp-1 link-1-1", "Level 05 sp-1 link-1-2",
                                     "Level 05 sp-2", "Level 05 sp-2 link-2"])
        self.assertEqual([self.cell(x, 6) for x in (22, 23, 24, 25, 26)], ["Pipe A - checked twice"] * 5)   # remarks everywhere
        self.assertTrue(all(self.fill(x) == "#00B050" for x in (23, 24, 26)))                                # new rows green
        self.assertTrue(all(self.cell(x, 3) == owner.capitalize() for x in (23, 24, 26)))
        self.assertEqual(self.cell(30, 3), "vara")                                                           # old rows moved down by 4, intact
        month = services.work_day().strftime("%Y-%m")
        night = sheet.get_book().tab("Night " + month).read_rows()
        self.assertEqual((len(night), night[0]["pipeline"], night[0]["remarks"]), (2, "Pipe A", "checked twice"))

    def test_end_of_shift_releases_unfinished_rows_and_clears_columns(self):
        self.start_shift(mode="auto")
        owner = Capture.objects.get(name="Level 05").owner.email.split("@")[0]
        cid, _ = self.begin(owner, "Level 05", 2)
        s1, s2 = Split.objects.filter(capture_id=cid).order_by("number")
        self.cl[owner].post(f"/api/splits/{s1.id}/start/"); self.cl[owner].post(f"/api/splits/{s1.id}/done/")
        self.cl[owner].post(f"/api/splits/{s2.id}/start/")                 # started, never finished
        r = self.cl["boss"].post("/api/reset-day/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((self.cell(22, 3), self.fill(22)), (owner.capitalize(), "#00B050"))   # finished split keeps name + green
        self.assertEqual((self.cell(23, 3), self.fill(23)), ("", None))                         # unfinished: free + colour removed
        summ = sheet.get_book().tab("Shift Summary").read_rows()
        self.assertEqual((summ[0]["splits done"], summ[0]["team total splits (shift)"]), ("1", "1"))
        tab = sheet.get_book().tab(services.settings.USERS_TAB)
        self.assertTrue(all(x["working today"] == "" for x in tab.read_rows()))
        # the unfinished split row is new work again tomorrow, the finished one is not
        self.assertEqual(self.cl["boss"].post("/api/sheet/sync/").data["new"], 3)
