import csv, os, shutil, tempfile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import services
from .models import AppState, Capture, DailyRoster, Split, User

SHEET = [["Project", "Capture", "Priority", "Resource", "Status"],
         ["P1", "C1", "Low", "", ""], ["P1", "C2", "High", "", ""], ["P2", "C3", "Medium", "", ""]]


class BaseSetup(TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", newline="") as f:
            csv.writer(f).writerows(SHEET)
        self.tabs = tempfile.mkdtemp()
        services._checked = services._fail_until = None
        self.override = override_settings(SHEET_CSV_PATH=self.path, GOOGLE_SHEET_ID="", SHEET_TABS_DIR=self.tabs,
                                          USERS_FROM_SHEET=False, GRAPH_CLIENT_ID="")
        self.override.enable()
        self.admin = User.objects.create_user("a@x.com", "pw123456", name="Boss", role="admin")
        self.u = {n: User.objects.create_user(f"{n}@x.com", "pw123456", name=n.title()) for n in ("asha", "ravi", "meena")}

    def tearDown(self):
        self.override.disable()
        os.remove(self.path)
        shutil.rmtree(self.tabs, ignore_errors=True)

    def api(self, user):
        c = APIClient(); c.force_authenticate(user); return c


class WorkflowTest(BaseSetup):
    def test_full_flow(self):
        adm = self.api(self.admin)
        self.assertEqual(adm.post("/api/sheet/sync/").data["new"], 3)
        self.assertEqual(adm.post("/api/sheet/sync/").data["new"], 0)  # idempotent

        r = adm.post("/api/roster/", {"emails": "asha@x.com, ravi@x.com, nobody@x.com", "distribute": True}, format="json")
        self.assertEqual(r.data["assigned"], 3)
        self.assertEqual(r.data["unknown"], ["nobody@x.com"])
        # high priority C2 goes out first; load is balanced 2 / 1
        loads = sorted(Capture.objects.filter(owner__isnull=False).values_list("owner__name", flat=True))
        self.assertEqual(sorted(set(loads)), ["Asha", "Ravi"])

        # sheet got the resource column
        rows = list(csv.reader(open(self.path)))
        self.assertTrue(rows[2][3])  # C2 row has a resource name

        # asha picks and declares 8 splits -> asks decision first
        asha = self.api(self.u["asha"])
        work = asha.get("/api/me/work/").data
        cap = work["captures"][0]
        self.assertEqual(asha.post(f"/api/captures/{cap['id']}/pick/").status_code, 200)
        r = asha.post(f"/api/captures/{cap['id']}/splits/", {"total": 8}, format="json")
        self.assertTrue(r.data["needs_decision"]); self.assertEqual(r.data["overflow"], 3)
        self.assertEqual(Split.objects.count(), 0)

        # asha chooses to hand over -> ravi receives splits 6-8
        r = asha.post(f"/api/captures/{cap['id']}/splits/", {"total": 8, "continue_all": False}, format="json")
        self.assertEqual((r.data["kept"], r.data["handed_over"], r.data["queued"]), (5, 3, 0))
        helper = Split.objects.filter(capture_id=cap["id"], number__gte=6).first().assignee
        self.assertEqual(helper.name, "Ravi")
        ravi_work = self.api(self.u["ravi"]).get("/api/me/work/").data
        self.assertEqual(len(ravi_work["split_tasks"]), 3)
        self.assertTrue(helper.notifications.filter(kind="task").exists())

        # finish everything -> capture completes with timestamps
        for s in Split.objects.filter(capture_id=cap["id"]):
            self.api(s.assignee).post(f"/api/splits/{s.id}/done/")
        c = Capture.objects.get(pk=cap["id"])
        self.assertEqual(c.status, "completed"); self.assertIsNotNone(c.completed_at)
        dash = adm.get("/api/dashboard/").data
        self.assertEqual(dash["stats"]["completed_today"], 1)

    def test_continue_all_and_nobody_free(self):
        adm = self.api(self.admin); adm.post("/api/sheet/sync/")
        DailyRoster.objects.create(date=services.work_day(), user=self.u["asha"])
        adm.post("/api/distribute/")
        asha = self.api(self.u["asha"])
        cap = asha.get("/api/me/work/").data["captures"][0]
        asha.post(f"/api/captures/{cap['id']}/pick/")
        r = asha.post(f"/api/captures/{cap['id']}/splits/", {"total": 7, "continue_all": False}, format="json")
        self.assertEqual(r.data["queued"], 2)  # nobody else on roster -> parked in queue
        # meena has nothing, presses "I need a task" -> gets the parked splits first
        self.assertEqual(self.api(self.u["meena"]).post("/api/me/request-task/").data["kind"], "split")

    def test_request_task_and_permissions(self):
        adm = self.api(self.admin); adm.post("/api/sheet/sync/")
        ravi = self.api(self.u["ravi"])
        self.assertEqual(ravi.get("/api/dashboard/").status_code, 403)
        r = ravi.post("/api/me/request-task/")
        self.assertEqual(r.data["kind"], "capture")
        self.assertEqual(Capture.objects.get(pk=r.data["id"]).name, "C2")  # highest priority
        self.assertEqual(ravi.post("/api/me/request-task/").status_code, 409)  # still has work

    def test_login(self):
        c = APIClient()
        self.assertEqual(c.post("/api/auth/login/", {"email": "asha@x.com", "password": "bad"}, format="json").status_code, 401)
        r = c.post("/api/auth/login/", {"email": "ASHA@x.com", "password": "pw123456"}, format="json")
        self.assertIn("token", r.data)


class DailyResetTest(BaseSetup):
    def test_new_day_wipes_everything_but_admin(self):
        adm = self.api(self.admin)
        adm.post("/api/roster/", {"emails": ["asha@x.com"], "distribute": True}, format="json")
        # first request of the day has already registered today's date; pretend it was yesterday
        AppState.objects.update_or_create(pk=1, defaults={"day": services.work_day() - __import__("datetime").timedelta(days=1)})
        services._checked = None
        self.assertEqual(adm.get("/api/dashboard/").status_code, 200)  # middleware resets here
        self.assertEqual(Capture.objects.count(), 0)
        self.assertEqual(DailyRoster.objects.count(), 0)
        self.assertEqual(list(User.objects.values_list("email", flat=True)), ["a@x.com"])

    def test_completed_rows_are_not_reimported(self):
        rows = list(csv.reader(open(self.path)))
        rows[1][4] = "Completed"
        csv.writer(open(self.path, "w", newline="")).writerows(rows)
        self.assertEqual(self.api(self.admin).post("/api/sheet/sync/").data["new"], 2)

    def test_paste_people_creates_accounts_and_times_reach_sheet(self):
        adm = self.api(self.admin)
        r = adm.post("/api/roster/", {"lines": "Neha, neha@x.com, secret99\nnew.guy@x.com", "distribute": True}, format="json")
        self.assertEqual(len(r.data["on_duty"]), 2)
        pw = {c["email"]: c["password"] for c in r.data["created"]}
        self.assertEqual(pw["neha@x.com"], "secret99")
        self.assertGreaterEqual(len(pw["new.guy@x.com"]), 6)  # auto-generated
        login = APIClient().post("/api/auth/login/", {"email": "neha@x.com", "password": "secret99"}, format="json")
        self.assertIn("token", login.data)
        # pick a capture -> Picked At is written to the sheet
        neha = APIClient(); neha.credentials(HTTP_AUTHORIZATION="Token " + login.data["token"])
        cap = neha.get("/api/me/work/").data["captures"][0]
        neha.post(f"/api/captures/{cap['id']}/pick/")
        rows = list(csv.reader(open(self.path)))
        self.assertIn("Picked At", rows[0])
        self.assertTrue(any(r[rows[0].index("Picked At")] for r in rows[1:] if len(r) > 5))
