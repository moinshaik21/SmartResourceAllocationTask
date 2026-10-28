from django.conf import settings
from django.core.management.base import BaseCommand

from core import services
from core.sheet import excel_enabled, get_book, get_sheet


class Command(BaseCommand):
    help = "Create the tabs of the CONTROL workbook (Resources, Pipelines, Today, Shift Summary) and list the admin."

    def add_arguments(self, p):
        p.add_argument("--sample", action="store_true", help="add 8 demo capture rows")

    def handle(self, *a, sample=False, **kw):
        self.stdout.write("Backend: " + ("Excel (Microsoft Graph)" if excel_enabled() else "local CSV / Google"))
        book = get_book()
        if not book:
            self.stdout.write("Google mode has no extra tabs - use Excel or local CSV."); return
        tabs = [(settings.USERS_TAB, services.USER_HEADERS), (settings.PIPELINES_TAB, ["Pipeline Name"]),
                ("Today", services.TODAY_HEADERS), ("Shift Summary", services.SUMMARY_HEADERS)]
        if not excel_enabled():
            tabs.insert(2, (settings.CAPTURES_TAB, services.CAPTURE_HEADERS))
        else:
            self.stdout.write(f"  your capture tab '{settings.CAPTURES_TAB}' is left untouched (run check_sheet to verify it)")
        for name, headers in tabs:
            book.tab(name, headers)
            self.stdout.write(f"  tab ready: {name}")
        from django.core.management import call_command
        call_command("ensure_admin")
        if sample and excel_enabled():
            self.stdout.write("  --sample skipped: never writes demo rows into your real Excel sheet")
        elif sample:
            rows = [[f"Project {p}", f"Cap-{p}{i}", pr, "", ""] for p in "AB"
                    for i, pr in enumerate(["High", "Medium", "Low", "High"], 1)]
            get_sheet().append(rows)
            self.stdout.write(f"  added {len(rows)} sample capture rows")
        self.stdout.write("Done.")
