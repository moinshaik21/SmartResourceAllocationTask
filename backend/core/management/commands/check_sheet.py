from django.conf import settings
from django.core.management.base import BaseCommand

from core.sheet import excel_enabled, get_sheet


class Command(BaseCommand):
    help = "Dry run: show how your capture tab is read (columns, window, new rows). Writes nothing."

    def handle(self, *a, **kw):
        tab = get_sheet()
        self.stdout.write(f"Backend: {'Excel' if excel_enabled() else 'local/Google'}   tab: {settings.CAPTURES_TAB}")
        if hasattr(tab, "describe"):
            d = tab.describe()
            self.stdout.write("Header row: " + ", ".join(f"{k}(col {v})" for k, v in d["headers"].items()))
            self.stdout.write("Mapped columns: " + ", ".join(f"{k}={v or '-'}" for k, v in d["cols"].items()))
            self.stdout.write(f"Last used row: {d['last']}   scanning rows {d['start']}..{d['last']}")
        rows = tab.read_rows()
        self.stdout.write(f"New (unassigned) captures found: {len(rows)}")
        for r in rows[:8]:
            self.stdout.write(f"  row {r['_row']}: {r.get('project')} | {r.get('capture')} | {r.get('ref', '')}")
        if len(rows) > 8:
            self.stdout.write(f"  ... and {len(rows) - 8} more")
