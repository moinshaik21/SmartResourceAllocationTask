import os
import re
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand

from core import ms_auth, sheet

QUOTES = ("'", '"')


def mask(v, keep=4):
    return "(empty)" if not v else f"{v[:keep]}... ({len(v)} chars)"


def host(url):
    return urlparse(url).netloc + "/..." if url else "(empty)"


class Command(BaseCommand):
    help = "Show which .env values the app loaded (secrets masked). Add --test to open the workbooks (read-only)."

    def add_arguments(self, p):
        p.add_argument("--test", action="store_true")

    def handle(self, *a, test=False, **kw):
        out, warn = self.stdout.write, []
        env_file = Path(settings.BASE_DIR) / ".env"
        out(f".env file: {env_file} -> {'FOUND' if env_file.exists() else 'NOT FOUND (only system environment variables are used)'}")
        if not env_file.exists():
            warn.append("There is no backend/.env file. Copy .env.example to .env (same folder as manage.py).")
        out("")
        secret = "set (app signs in by itself)" if settings.GRAPH_CLIENT_SECRET else "empty (admin signs in once from the dashboard)"
        rows = [
            ("GRAPH_TENANT_ID", mask(settings.GRAPH_TENANT_ID)), ("GRAPH_CLIENT_ID", mask(settings.GRAPH_CLIENT_ID)),
            ("GRAPH_CLIENT_SECRET", secret),
            ("GRAPH_FILE_URL (control workbook)", host(settings.GRAPH_FILE_URL)),
            ("GRAPH_CAPTURES_URL (production)", host(settings.GRAPH_CAPTURES_URL) if settings.GRAPH_CAPTURES_URL else "(empty - paste it on the dashboard)"),
            ("CAPTURES_TAB", repr(settings.CAPTURES_TAB)),
            ("Project / Capture / JOB ID / Resource / Remarks", " | ".join(repr(x) for x in (
                settings.CAPTURE_COL_PROJECT, settings.CAPTURE_COL_CAPTURE, settings.CAPTURE_COL_REF,
                settings.CAPTURE_COL_RESOURCE, settings.CAPTURE_COL_REMARKS))),
            ("DAY_START_HOUR / TIME_ZONE", f"{settings.DAY_START_HOUR} / {settings.TIME_ZONE}"),
            ("LOG_TAB_PREFIX", repr(settings.LOG_TAB_PREFIX)), ("USERS_FROM_SHEET", settings.USERS_FROM_SHEET),
            ("AUTO_SYNC_SECONDS", settings.AUTO_SYNC_SECONDS),
            ("Database", settings.DATABASES["default"]["ENGINE"].split(".")[-1]),
            ("ADMIN_EMAIL", os.environ.get("ADMIN_EMAIL") or "(empty)"),
        ]
        for k, v in rows:
            out(f"  {k:<50} {v}")
        mode = "Excel" if sheet.excel_enabled() else ("Google (legacy)" if settings.GOOGLE_SHEET_ID else "LOCAL CSV FILES")
        out(f"\nBackend in use: {mode}")
        if mode == "LOCAL CSV FILES":
            warn.append("Excel is NOT active: GRAPH_TENANT_ID, GRAPH_CLIENT_ID and GRAPH_FILE_URL must all be filled.")
        for name in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_FILE_URL", "GRAPH_CAPTURES_URL"):
            v = getattr(settings, name)
            if v and (v != v.strip() or v[0] in QUOTES or v[-1] in QUOTES):
                warn.append(f"{name} has quotes or spaces around it. Write it as NAME=value without quotes.")
        if settings.GRAPH_FILE_URL and not settings.GRAPH_FILE_URL.startswith("https://"):
            warn.append("GRAPH_FILE_URL must start with https://")
        if settings.GRAPH_CLIENT_SECRET and re.fullmatch(r"[0-9a-fA-F-]{36}", settings.GRAPH_CLIENT_SECRET):
            warn.append("GRAPH_CLIENT_SECRET looks like a Secret ID. Use the secret VALUE, or leave it empty to sign in from the dashboard.")
        if settings.DAY_START_HOUR in (0, 7):
            warn.append(f"DAY_START_HOUR={settings.DAY_START_HOUR}: for a 7pm-7am shift use 12, so the reset happens when nobody works.")
        if mode == "Excel" and not settings.GRAPH_CLIENT_SECRET:
            try:
                st = ms_auth.status()
            except Exception:  # noqa: BLE001 - tables missing
                warn.append("The database is not ready: run  python manage.py migrate  first.")
            else:
                out(f"Microsoft sign-in: {st['state']}" + (f" as {st['account']}" if st["account"] else ""))
                if st["state"] != "connected":
                    warn.append("Microsoft is not connected yet: open the admin dashboard and click 'Sign in with Microsoft' "
                                "(or run: python manage.py ms_login).")
        if test:
            if mode != "Excel":
                warn.append("--test needs Excel mode (see above).")
            else:
                out("\nConnection test (read-only):")
                sheet.reset_cache()
                for label, url in (("Control workbook", settings.GRAPH_FILE_URL), ("Production workbook", settings.GRAPH_CAPTURES_URL)):
                    if not url:
                        out(f"  - {label}: skipped (no link)")
                        continue
                    try:
                        sheet._item(url)
                        out(f"  OK   {label}")
                    except Exception as e:  # noqa: BLE001
                        out(f"  FAIL {label}: {e}")
        out("")
        for w in warn:
            out(f"  ! {w}")
        if not warn:
            out("No problems found.")
