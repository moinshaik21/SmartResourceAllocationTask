import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status as http
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import AppState, Capture, DailyRoster, Notification, Project, Split, Transfer, User
from .serializers import (CaptureDetailSerializer, CaptureSerializer,
                          NotificationSerializer, SplitSerializer, UserSerializer)


class IsAdminRole(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == "admin")


def exception_handler(exc, ctx):
    """Microsoft problems get a clear JSON answer the frontend can act on (ms_expired -> 'sign in again')."""
    from rest_framework.views import exception_handler as drf_handler
    from .ms_auth import NotConnected
    from .sheet import GraphError
    if isinstance(exc, NotConnected):
        return Response({"detail": str(exc), "code": "ms_expired"}, status=http.HTTP_503_SERVICE_UNAVAILABLE)
    if isinstance(exc, GraphError):
        return Response({"detail": f"Excel did not answer as expected: {exc}", "code": "excel_error"},
                        status=http.HTTP_502_BAD_GATEWAY)
    return drf_handler(exc, ctx)


def err(message, code=http.HTTP_400_BAD_REQUEST):
    return Response({"detail": message}, status=code)


def captures_qs():
    return (Capture.objects.select_related("project", "owner")
            .prefetch_related(Prefetch("splits", queryset=Split.objects.select_related("assignee"))))


# ---------- auth ------------------------------------------------------------
class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        try:
            services.sync_users_from_sheet(force=True)   # Users tab is the source of truth
        except Exception:  # noqa: BLE001 - sheet down: fall back to the last synced accounts
            logging.getLogger(__name__).exception("Users tab sync failed at login")
        user = authenticate(request, username=email, password=request.data.get("password") or "")
        if not user:
            return err("Wrong email or password.", http.HTTP_401_UNAUTHORIZED)
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data})


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


# ---------- admin: dashboard -----------------------------------------------
class DashboardView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        try:
            services.maybe_autosync()          # new captures from Excel appear here by themselves
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("autosync failed")
        today = services.work_day()
        roster_ids = set(DailyRoster.objects.filter(date=today).values_list("user_id", flat=True))
        open_caps = captures_qs().filter(status__in=Capture.OPEN)
        by_owner = {}
        for c in open_caps:
            by_owner.setdefault(c.owner_id, []).append(CaptureSerializer(c).data)

        helper_splits = {}
        for s in (Split.objects.select_related("capture__project", "assignee")
                  .exclude(status=Split.DONE).exclude(assignee=None)):
            if s.assignee_id != s.capture.owner_id:
                helper_splits.setdefault(s.assignee_id, []).append(
                    {"id": s.id, "number": s.number, "status": s.status,
                     "capture_name": s.capture.name, "project_name": s.capture.project.name})

        resources = []
        for u in User.objects.filter(role="resource", is_active=True).order_by("name"):
            resources.append({
                "id": u.id, "name": u.name, "email": u.email,
                "on_duty": u.id in roster_ids,
                "captures": by_owner.get(u.id, []),
                "helper_splits": helper_splits.get(u.id, []),
                "completed_today": Capture.objects.filter(owner=u, status=Capture.COMPLETED).count(),
                "splits_today": Split.objects.filter(assignee=u, status=Split.DONE).count(),
                "senior_for": u.senior_for,
            })

        stats = {
            "projects": Project.objects.count(),
            "unassigned": Capture.objects.filter(status=Capture.NEW).count(),
            "in_progress": Capture.objects.filter(status__in=Capture.OPEN).count(),
            "completed_today": Capture.objects.filter(status=Capture.COMPLETED).count(),
            "completed_total": Capture.objects.filter(status=Capture.COMPLETED).count(),
            "on_duty": len(roster_ids),
            "transfers_open": Transfer.objects.filter(status="open").count(),
            "wrapups_pending": Capture.objects.filter(status=Capture.COMPLETED, wrapped_up=False).count(),
            "queued_splits": Split.objects.filter(assignee=None, capture__status=Capture.WORKING).exclude(status=Split.DONE).count(),
        }
        st = services.get_state()
        sync = {"last_sync_at": st.last_sync_at, "last_sync_new": st.last_sync_new, "error": st.last_sync_error,
                "start_row": st.start_row, "auto": bool(settings.AUTO_SYNC_SECONDS), "auto_allocate": st.auto_allocate}
        return Response({"day": str(today), "stats": stats, "resources": resources, "sync": sync})


class CaptureListView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        qs = captures_qs()
        if st := request.query_params.get("status"):
            qs = qs.filter(status=st)
        if q := request.query_params.get("q"):
            qs = qs.filter(name__icontains=q) | qs.filter(project__name__icontains=q)
        qs = qs.order_by("-picked_at", "priority", "-created_at")[:300]
        return Response(CaptureSerializer(qs, many=True).data)


class CaptureAssignView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        cap = get_object_or_404(Capture, pk=pk)
        user = get_object_or_404(User, pk=request.data.get("user_id"), role="resource", is_active=True)
        try:
            cap = services.assign_capture(cap, user, by=request.user)
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_409_CONFLICT)
        return Response(CaptureSerializer(captures_qs().get(pk=cap.pk)).data)


class ProjectListView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        caps = captures_qs()
        grouped = {}
        for c in caps:
            grouped.setdefault(c.project_id, []).append(c)
        out = []
        for p in Project.objects.all():
            cs = grouped.get(p.id, [])
            done = sum(1 for c in cs if c.status == Capture.COMPLETED)
            out.append({"id": p.id, "name": p.name, "created_at": p.created_at,
                        "total": len(cs), "completed": done,
                        "captures": CaptureSerializer(cs, many=True).data})
        return Response(out)


class SyncView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        try:
            return Response(services.sync_from_sheet())
        except Exception as e:  # noqa: BLE001
            return err(f"Could not read the sheet: {e}", http.HTTP_502_BAD_GATEWAY)


class CronSyncView(APIView):
    """Free-tier hosts sleep, so let an external cron ping this to pull new rows."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        if not settings.CRON_SECRET or request.query_params.get("key") != settings.CRON_SECRET:
            return err("Forbidden", http.HTTP_403_FORBIDDEN)
        from .sheet import excel_enabled
        st = services.get_state()
        if excel_enabled() and not st.start_row:
            return Response({"skipped": "The admin has not typed today's start row yet."})
        result = services.sync_from_sheet()
        result["distributed"] = 0
        if not excel_enabled() or st.auto_allocate:
            try:
                result["distributed"] = services.distribute()
            except services.WorkflowError:
                pass
        return Response(result)


class DistributeView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        try:
            return Response({"assigned": services.distribute()})
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_409_CONFLICT)


# ---------- admin: resources & roster --------------------------------------
class ResourceListView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        try:
            services.sync_users_from_sheet()
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("Users tab sync failed")
        return Response(UserSerializer(User.objects.filter(role="resource").order_by("name"), many=True).data)

    def post(self, request):
        name = (request.data.get("name") or "").strip()
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        if not name or not email or len(password) < 6:
            return err("Name, email and a password of at least 6 characters are required.")
        if User.objects.filter(email=email).exists():
            return err("A user with this email already exists.", http.HTTP_409_CONFLICT)
        user = User.objects.create_user(email=email, password=password, name=name, role="resource",
                                        senior_for=(request.data.get("senior_for") or "").strip())
        try:
            services.sheet_upsert_user(user)
        except Exception as e:  # noqa: BLE001
            user.delete()
            return err(f"Could not save to the Users tab: {e}", http.HTTP_502_BAD_GATEWAY)
        return Response(UserSerializer(user).data, status=http.HTTP_201_CREATED)


class ResourceDetailView(APIView):
    permission_classes = [IsAdminRole]

    def patch(self, request, pk):
        user = get_object_or_404(User, pk=pk, role="resource")
        if "is_active" in request.data:
            user.is_active = bool(request.data["is_active"])
        if request.data.get("password"):
            if len(request.data["password"]) < 6:
                return err("Password must be at least 6 characters.")
            user.set_password(request.data["password"])
        if request.data.get("name"):
            user.name = request.data["name"].strip()
        if "senior_for" in request.data:
            user.senior_for = (request.data.get("senior_for") or "").strip()
        user.save()
        try:
            services.sheet_upsert_user(user)
        except Exception as e:  # noqa: BLE001
            return err(f"Saved, but the Users tab could not be updated: {e}", http.HTTP_502_BAD_GATEWAY)
        return Response(UserSerializer(user).data)


class RosterView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        return Response(UserSerializer(services.roster_users(), many=True).data)

    def post(self, request):
        raw = request.data.get("emails") or []
        if isinstance(raw, str):
            raw = raw.replace(";", ",").replace("\n", ",").split(",")
        emails = sorted({e.strip().lower() for e in raw if e.strip()})

        try:
            services.sync_users_from_sheet()
            # new people typed/pasted for today -> accounts are created on the spot (and saved to the Users tab)
            new_users, creds = services.add_people(services.parse_people(request.data.get("lines", "")))
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_502_BAD_GATEWAY)
        users, unknown = services.set_roster(emails, new_users)
        assigned = 0
        if request.data.get("distribute") and users:
            try:
                services.sync_from_sheet()
            except Exception:  # noqa: BLE001
                pass
            assigned = services.distribute()
        return Response({"on_duty": UserSerializer(users.values(), many=True).data,
                         "unknown": unknown, "assigned": assigned, "created": creds})


class ResetDayView(APIView):
    """Manual 'start a new day' (it also happens automatically at the day change)."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        try:
            return Response(services.end_shift())
        except Exception as e:  # noqa: BLE001
            return err(f"Could not archive today's work to the sheet, so nothing was cleared: {e}",
                       http.HTTP_502_BAD_GATEWAY)


# ---------- resource: my work ----------------------------------------------
class MyWorkView(APIView):
    def get(self, request):
        u = request.user
        mine = captures_qs().filter(owner=u, status__in=Capture.OPEN)
        helper = (Split.objects.select_related("capture__project", "capture__owner")
                  .filter(assignee=u).exclude(status=Split.DONE).exclude(capture__owner=u))
        return Response({
            "captures": CaptureDetailSerializer(mine, many=True).data,
            "split_tasks": [{
                **SplitSerializer(s).data,
                "capture_id": s.capture_id, "capture_name": s.capture.name,
                "project_name": s.capture.project.name, "from_name": s.capture.owner.name if s.capture.owner else "",
            } for s in helper],
            "completed_today": Capture.objects.filter(owner=u, status=Capture.COMPLETED).count(),
            "wrapup_pending": CaptureDetailSerializer(
                captures_qs().filter(owner=u, status=Capture.COMPLETED, wrapped_up=False), many=True).data,
            "limit": settings.MAX_SPLITS_PER_PERSON,
        })


class RequestTaskView(APIView):
    def post(self, request):
        try:
            return Response(services.request_task(request.user))
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_409_CONFLICT)


class PickView(APIView):
    def post(self, request, pk):
        cap = get_object_or_404(Capture, pk=pk)
        try:
            services.pick_capture(cap, request.user)
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_409_CONFLICT)
        return Response(CaptureDetailSerializer(captures_qs().get(pk=pk)).data)


class DeclareSplitsView(APIView):
    def post(self, request, pk):
        cap = get_object_or_404(Capture, pk=pk)
        try:
            total = int(request.data.get("total"))
        except (TypeError, ValueError):
            return err("Enter the number of splits as a whole number.")
        if total > 500:
            return err("That's too many splits for one capture (max 500).")
        try:
            result = services.declare_splits(cap, request.user, total, request.data.get("continue_all"))
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_409_CONFLICT)
        return Response(result)


class SplitActionView(APIView):
    def post(self, request, pk, action):
        split = get_object_or_404(Split, pk=pk)
        try:
            if action == "start":
                services.start_split(split, request.user)
            elif action == "done":
                services.finish_split(split, request.user)
            else:
                return err("Unknown action.", http.HTTP_404_NOT_FOUND)
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_409_CONFLICT)
        return Response(SplitSerializer(split).data)


# ---------- notifications ---------------------------------------------------
class NotificationsView(APIView):
    def get(self, request):
        qs = Notification.objects.filter(user=request.user)[:30]
        return Response({"items": NotificationSerializer(qs, many=True).data,
                         "unread": Notification.objects.filter(user=request.user, is_read=False).count()})

    def post(self, request):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({"ok": True})


# ---------- today's setup: start row, resources from Excel, allocation --------------------------
class SheetConfigView(APIView):
    """GET/POST the production workbook link, tab and the row to start from (typed by the admin each day)."""
    permission_classes = [IsAdminRole]

    @staticmethod
    def _data():
        from .sheet import runtime
        rt, st = runtime(), services.get_state()
        return {"captures_url": st.captures_url, "captures_tab": st.captures_tab or settings.CAPTURES_TAB,
                "start_row": st.start_row, "end_row": st.end_row,
                "default_url": bool(settings.GRAPH_CAPTURES_URL), "effective_tab": rt["tab"]}

    def get(self, request):
        return Response(self._data())

    def post(self, request):
        st = services.get_state()
        d = request.data
        def num(k):
            v = d.get(k)
            if v in (None, ""):
                return None
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise services.WorkflowError(f"{k.replace('_', ' ').capitalize()} must be a whole number.")
            if v < 2:
                raise services.WorkflowError("Rows start at 2 (row 1 is the header).")
            return v
        try:
            st.start_row, st.end_row = num("start_row"), num("end_row")
            if st.start_row and st.end_row and st.end_row < st.start_row:
                raise services.WorkflowError("End row must be after the start row.")
        except services.WorkflowError as e:
            return err(str(e))
        if "captures_url" in d:
            st.captures_url = (d.get("captures_url") or "").strip()
        if "captures_tab" in d:
            st.captures_tab = (d.get("captures_tab") or "").strip()
        st.save()
        return Response(self._data())


class StartShiftView(APIView):
    """One click: save the start row, fetch today's people from Excel, read new captures, allocate."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        cfg = SheetConfigView()
        resp = cfg.post(request)
        if resp.status_code >= 400:
            return resp
        out = {"config": resp.data}
        try:
            emails = services.roster_emails_from_sheet() if request.data.get("fetch_resources", True) else None
            if emails is not None:
                if not emails:
                    return err("No one has Y in the 'Working Today' column of the Resources tab.")
                users, unknown = services.set_roster(emails)
                out["on_duty"] = UserSerializer(users.values(), many=True).data
                out["unknown"] = unknown
            out["sync"] = services.sync_from_sheet()
            mode = request.data.get("mode")
            st = services.get_state()
            st.auto_allocate = mode == "auto"
            st.save(update_fields=["auto_allocate"])
            out["assigned"] = services.distribute() if mode == "auto" else 0
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_409_CONFLICT)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).exception("start shift failed")
            return err(f"Could not read Excel: {e}", http.HTTP_502_BAD_GATEWAY)
        return Response(out)


class RosterFetchView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        try:
            emails = services.roster_emails_from_sheet()
        except Exception as e:  # noqa: BLE001
            return err(f"Could not read the Resources tab: {e}", http.HTTP_502_BAD_GATEWAY)
        if not emails:
            return err("No one has Y in the 'Working Today' column of the Resources tab.")
        users, unknown = services.set_roster(emails)
        return Response({"on_duty": UserSerializer(users.values(), many=True).data, "unknown": unknown})


# ---------- transfer to admin -> senior ----------------------------------------------------------
class CaptureTransferView(APIView):
    def post(self, request, pk):
        cap = get_object_or_404(Capture, pk=pk)
        try:
            services.request_transfer(cap, request.user, request.data.get("reason"))
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_409_CONFLICT)
        return Response({"ok": True})


class TransferListView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        out = []
        roster = services.roster_users()
        for t in Transfer.objects.filter(status="open").select_related("capture__project", "from_user").order_by("created_at"):
            seniors = services.seniors_for(t.capture)
            ids = {u.pk for u in seniors}
            out.append({
                "id": t.id, "reason": t.reason, "created_at": t.created_at,
                "capture_id": t.capture_id, "capture_name": t.capture.name, "project_name": t.capture.project.name,
                "from_name": t.from_user.name if t.from_user else "",
                "seniors": [{"id": u.id, "name": u.name, "on_duty": u in roster} for u in seniors],
                "others": [{"id": u.id, "name": u.name} for u in roster if u.pk not in ids and u.pk != t.from_user_id],
            })
        return Response(out)


class TransferResolveView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        t = get_object_or_404(Transfer, pk=pk)
        try:
            services.resolve_transfer(t, request.data.get("user_id"), request.user)
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_409_CONFLICT)
        return Response({"ok": True})


# ---------- wrap-up --------------------------------------------------------------------------------
class PipelinesView(APIView):
    def get(self, request):
        try:
            return Response({"pipelines": services.pipelines_list()})
        except Exception:  # noqa: BLE001
            return Response({"pipelines": []})


class WrapupView(APIView):
    def post(self, request, pk):
        cap = get_object_or_404(Capture, pk=pk)
        try:
            services.wrapup_capture(cap, request.user, request.data.get("pipeline"), request.data.get("remarks"),
                                    request.data.get("sublinks") or {})
        except services.WorkflowError as e:
            return err(str(e), http.HTTP_409_CONFLICT)
        return Response(CaptureDetailSerializer(captures_qs().get(pk=pk)).data)


# ---------- Microsoft connection (admin dashboard only) ----------------------------------------------
class MsStatusView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        from . import ms_auth
        return Response(ms_auth.status())


class MsConnectView(APIView):
    """Starts the one-time Microsoft sign-in (device code). The dashboard shows the code and polls /ms/status/."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        from . import ms_auth
        from .sheet import excel_enabled
        if not excel_enabled():
            return err("Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID and GRAPH_FILE_URL first.")
        if settings.GRAPH_CLIENT_SECRET:
            return err("A client secret is set, so no sign-in is needed.")
        try:
            return Response(ms_auth.start_device_flow())
        except Exception as e:  # noqa: BLE001
            return err(f"Could not start the Microsoft sign-in: {e}", http.HTTP_502_BAD_GATEWAY)


class MsDisconnectView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        from . import ms_auth
        ms_auth.disconnect()
        return Response(ms_auth.status())
