"""Microsoft sign-in (device code, token in the DB), expiry handling, the Graph call wrapper and auto-sync."""
import time
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import ms_auth, services, sheet
from .models import AppState, Capture, MsToken, User
from .tests import BaseSetup
from .tests_sheet import FakeGraph, MAIN, urow

DELEGATED = dict(GRAPH_TENANT_ID="tenant", GRAPH_CLIENT_ID="client", GRAPH_CLIENT_SECRET="",
                 GRAPH_FILE_URL="https://x/control.xlsx", GRAPH_CAPTURES_URL="https://x/production.xlsx", GOOGLE_SHEET_ID="")


class FakeMsal:
    """Stands in for msal.PublicClientApplication."""
    silent = {"access_token": "TOKEN-1", "expires_in": 3600}
    accounts = [{"username": "boss@x.com"}]
    flow_result = {"access_token": "TOKEN-0", "expires_in": 3600, "id_token_claims": {"preferred_username": "boss@x.com"}}

    def __init__(self, client_id, authority=None, token_cache=None):
        self.cache = token_cache
    def initiate_device_flow(self, scopes):
        return {"user_code": "ABCD-1234", "verification_uri": "https://microsoft.com/devicelogin", "expires_in": 900,
                "message": "go sign in", "expires_at": time.time() + 900}
    def acquire_token_by_device_flow(self, flow):
        return dict(self.flow_result)
    def get_accounts(self): return list(self.accounts)
    def acquire_token_silent(self, scopes, account=None, force_refresh=False):
        return dict(self.silent) if self.silent is not None else None


class SyncThread:
    """Runs the device-flow wait immediately instead of in a background thread."""
    def __init__(self, target, args=(), daemon=None): self.target, self.args = target, args
    def start(self): self.target(*self.args)


class MicrosoftSignInTest(BaseSetup):
    def setUp(self):
        super().setUp()
        self.o2 = override_settings(**DELEGATED); self.o2.enable(); self.addCleanup(self.o2.disable)
        FakeMsal.silent = {"access_token": "TOKEN-1", "expires_in": 3600}
        FakeMsal.accounts = [{"username": "boss@x.com"}]
        ms_auth._tok.update(v=None, exp=0.0); ms_auth._flow.update(state="idle", error="")
        for p in (mock.patch.object(ms_auth.msal, "PublicClientApplication", FakeMsal),
                  mock.patch.object(ms_auth.threading, "Thread", SyncThread),
                  mock.patch.object(services, "after_connect", lambda: None)):
            p.start(); self.addCleanup(p.stop)
        self.adm = self.api(self.admin)

    def test_needs_only_tenant_client_and_link(self):
        self.assertTrue(sheet.excel_enabled())
        self.assertEqual(self.adm.get("/api/ms/status/").data["state"], "none")
        with self.assertRaises(ms_auth.NotConnected):
            ms_auth.get_token()                                     # nobody signed in yet

    def test_admin_signs_in_once_and_the_token_is_stored_encrypted(self):
        r = self.adm.post("/api/ms/connect/")
        self.assertEqual((r.status_code, r.data["user_code"]), (200, "ABCD-1234"))
        st = self.adm.get("/api/ms/status/").data
        self.assertEqual((st["state"], st["account"]), ("connected", "boss@x.com"))
        row = MsToken.objects.get(pk=1)
        self.assertTrue(row.cache and "TOKEN" not in row.cache)     # stored, but not readable text
        ms_auth._tok.update(v=None, exp=0.0)                         # simulate a restart
        self.assertEqual(ms_auth.get_token(), "TOKEN-1")             # refreshed silently from the DB cache

    def test_only_admins_can_connect(self):
        self.assertEqual(self.api(self.u["asha"]).post("/api/ms/connect/").status_code, 403)
        self.assertEqual(self.api(self.u["asha"]).get("/api/ms/status/").status_code, 403)

    def test_expired_sign_in_is_reported_to_the_frontend(self):
        self.adm.post("/api/ms/connect/")
        ms_auth._tok.update(v=None, exp=0.0)
        FakeMsal.silent = {"error": "invalid_grant", "error_description": "refresh token expired"}
        with self.assertRaises(ms_auth.NotConnected):
            ms_auth.get_token()
        st = self.adm.get("/api/ms/status/").data
        self.assertEqual(st["state"], "expired"); self.assertIn("expired", st["error"])
        # signing in again repairs it
        FakeMsal.flow_result = {**FakeMsal.flow_result, "access_token": "TOKEN-2"}
        self.adm.post("/api/ms/connect/")
        self.assertEqual(self.adm.get("/api/ms/status/").data["state"], "connected")
        self.assertEqual(ms_auth.get_token(), "TOKEN-2")

    def test_network_hiccup_is_not_treated_as_expiry(self):
        self.adm.post("/api/ms/connect/")
        ms_auth._tok.update(v=None, exp=0.0)
        FakeMsal.silent = {"error": "temporarily_unavailable", "error_description": "try later"}
        with self.assertRaises(RuntimeError) as cm:
            ms_auth.get_token()
        self.assertNotIsInstance(cm.exception, ms_auth.NotConnected)
        self.assertEqual(MsToken.objects.get(pk=1).status, "connected")

    def test_disconnect(self):
        self.adm.post("/api/ms/connect/")
        self.assertEqual(self.adm.post("/api/ms/disconnect/").data["state"], "none")
        self.assertFalse(MsToken.objects.exists())

    def test_expired_error_has_a_code(self):
        from . import views
        r = views.exception_handler(ms_auth.NotConnected("sign in again"), {})
        self.assertEqual((r.status_code, r.data["code"]), (503, "ms_expired"))

    def test_not_connected_does_not_lock_the_admin_out(self):
        # Microsoft not connected yet: the admin created by ADMIN_EMAIL/ADMIN_PASSWORD must still be able to log in
        with override_settings(USERS_FROM_SHEET=True):
            r = APIClient().post("/api/auth/login/", {"email": "a@x.com", "password": "pw123456"}, format="json")
            self.assertEqual(r.status_code, 200, r.data)


class GraphCallTest(TestCase):
    """The HTTP wrapper: bearer token, workbook session, refresh on 401, retry on throttling."""
    def setUp(self):
        sheet.reset_cache()
        self.o = override_settings(**DELEGATED); self.o.enable(); self.addCleanup(self.o.disable)
        self.tokens = []
        def tok(force=False): self.tokens.append(force); return "T2" if force else "T1"
        for p in (mock.patch.object(sheet, "_token", tok), mock.patch.object(sheet.time, "sleep", lambda s: None)):
            p.start(); self.addCleanup(p.stop)

    def resp(self, status, json=None, headers=None):
        r = mock.Mock(); r.status_code, r.content = status, b"x" if json is not None else b""
        r.json.return_value = json or {}; r.text = str(json); r.headers = headers or {}
        return r

    def test_session_header_refresh_on_401_and_retry_on_429(self):
        url = "/drives/D/items/I/workbook/worksheets/Sheet1/usedRange"
        seq = [self.resp(200, {"id": "SESS"}),                 # createSession
               self.resp(401, {"error": "expired"}),           # first real call: token expired
               self.resp(429, {"error": "slow"}, {"Retry-After": "1"}),
               self.resp(200, {"values": [[1]]})]
        with mock.patch("requests.request", side_effect=seq) as rq:
            out = sheet._call("GET", url)
        self.assertEqual(out, {"values": [[1]]})
        self.assertTrue(any(c.kwargs["headers"].get("workbook-session-id") == "SESS" for c in rq.call_args_list[1:]))
        self.assertIn(True, self.tokens)                       # a forced token refresh happened after the 401
        self.assertEqual(rq.call_args_list[0].args[:2][0], "POST")

    def test_real_errors_are_raised(self):
        with mock.patch("requests.request", side_effect=[self.resp(200, {"id": "S"}), self.resp(404, {"error": "nf"})]):
            with self.assertRaises(sheet.GraphError) as cm:
                sheet._call("GET", "/drives/D/items/I/workbook/worksheets/Nope")
        self.assertEqual(cm.exception.status, 404)


@override_settings(**DELEGATED, USERS_FROM_SHEET=True, CAPTURES_TAB=MAIN, CAPTURE_COL_PROJECT="Projects",
                   CAPTURE_COL_CAPTURE="Structure", CAPTURE_COL_REF="JOB ID", CAPTURE_COL_PRIORITY="",
                   CAPTURE_COL_STATUS="", CAPTURE_WRITEBACK=["resource"], SPLIT_ROWS_MODE="insert", NAME_ON_START=True,
                   AUTO_SYNC_SECONDS=90)
class AutoSyncAndV4SheetTest(TestCase):
    """The sheet as captureautomate (V4) writes it: D=Projects, E=Structure Name, F=Date of Capture, H=Capture path."""
    def setUp(self):
        sheet.reset_cache(); services._checked = services._fail_until = None; services._users_synced = 0.0
        self.fake = FakeGraph()
        for p in (mock.patch.object(sheet, "_call", self.fake), mock.patch.object(sheet, "_item", lambda url=None: "/ITEM")):
            p.start(); self.addCleanup(p.stop)
        heads = ["Notes", "JOB ID", "Resource", "Projects", "Structure Name", "Date of Capture", "Upload email time", "Capture path"]
        v = {(1, c): h for c, h in enumerate(heads, 1)}
        for i, (proj, st, path) in enumerate([("200 - NIH SRLM", "Level 1", "s3://a/1"), ("200 - NIH SRLM", "Level 2", "s3://a/2"),
                                              ("300 - Tower", "Level 1", "s3://b/1")]):
            r = 22 + i
            v.update({(r, 4): proj, (r, 5): st, (r, 6): 45936.0, (r, 8): path})        # V4 never fills B or C
        self.fake.ws[MAIN] = {"v": v, "f": {}, "fill": {}}
        sheet.get_book().tab(services.settings.USERS_TAB, services.USER_HEADERS).append([
            urow("Boss", "boss@x.com", "bosspw12", "admin"), urow("Asha", "asha@x.com", "ashapw12", working="Y")])
        with mock.patch.object(sheet, "_token", lambda force=False: "T"):
            pass
        r = APIClient().post("/api/auth/login/", {"email": "boss@x.com", "password": "bosspw12"}, format="json")
        self.boss = APIClient(); self.boss.credentials(HTTP_AUTHORIZATION="Token " + r.data["token"])

    def test_header_names_are_matched_loosely_and_path_date_are_read(self):
        self.boss.post("/api/shift/start/", {"start_row": 22, "mode": "manual"}, format="json")
        caps = {c.capture_path: c for c in Capture.objects.all()}
        self.assertEqual(set(caps), {"s3://a/1", "s3://a/2", "s3://b/1"})               # 'Structure' found 'Structure Name'
        c = caps["s3://a/1"]
        self.assertEqual((c.name, c.ref), ("Level 1", "s3://a/1"))
        self.assertRegex(c.captured_on, r"^\d{2} \w{3} \d{4}$")

    def test_new_rows_written_by_captureautomate_appear_by_themselves(self):
        self.boss.post("/api/shift/start/", {"start_row": 22, "mode": "manual"}, format="json")
        self.assertEqual(Capture.objects.count(), 3)
        w = self.fake.ws[MAIN]["v"]
        w.update({(25, 4): "300 - Tower", (25, 5): "Level 2", (25, 8): "s3://b/2"})        # V4 appends a capture
        AppState.objects.update(last_sync_at=timezone.now() - timedelta(seconds=30))
        d = self.boss.get("/api/dashboard/").data
        self.assertEqual(Capture.objects.count(), 3)                                      # checked less than 90s ago
        AppState.objects.update(last_sync_at=timezone.now() - timedelta(seconds=120))
        d = self.boss.get("/api/dashboard/").data
        self.assertEqual(Capture.objects.count(), 4)
        self.assertEqual((d["sync"]["last_sync_new"], d["sync"]["start_row"], d["stats"]["unassigned"]), (1, 22, 4))

    def test_autosync_waits_for_the_start_row(self):
        self.assertIsNone(services.maybe_autosync())
        self.assertEqual(Capture.objects.count(), 0)                                      # no start row: nothing imported

    def test_auto_mode_allocates_new_captures_as_they_arrive(self):
        self.boss.post("/api/shift/start/", {"start_row": 22, "mode": "auto"}, format="json")
        self.fake.ws[MAIN]["v"].update({(25, 4): "300 - Tower", (25, 5): "Level 2", (25, 8): "s3://b/2"})
        AppState.objects.update(last_sync_at=timezone.now() - timedelta(seconds=120))
        self.boss.get("/api/dashboard/")
        self.assertEqual(Capture.objects.filter(status="assigned").count(), 4)

    def test_daily_grid_and_status_for_everyone_on_duty(self):
        self.boss.post("/api/shift/start/", {"start_row": 22, "mode": "manual"}, format="json")
        self.assertEqual(self.boss.post("/api/reset-day/").status_code, 200)               # Asha was on duty, did nothing
        summ = sheet.get_book().tab("Shift Summary").read_rows()
        self.assertEqual((summ[0]["name"], summ[0]["status"], summ[0]["splits done"]), ("Asha", "On duty - no splits", "0"))
        grid = sheet.get_book().tab("Daily Work " + services.work_day().strftime("%Y-%m")).read_rows()
        self.assertEqual((grid[0]["name"], grid[0][str(services.work_day().day)], grid[0]["total"]), ("Asha", "0", "0"))
