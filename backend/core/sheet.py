"""Spreadsheet access.

Three backends, chosen automatically:
  * Excel (.xlsx on OneDrive/SharePoint via Microsoft Graph + Entra app)   -> GRAPH_* settings
  * Google Sheet (legacy; Captures tab only, no Users/log tabs)           -> GOOGLE_SHEET_ID
  * Local CSV files (offline demo / tests)                                -> nothing configured

Every tab (Captures, Users, Today, Shift Summary, 2026-10 ...) is a `Tab` with the same methods:
read_rows(), update_row(), append(), reset(), ensure_headers().
Captures columns: Project | Capture | Priority | Resource | Status (+ Splits, Picked At, Completed At added by the app).
"""
import base64
import csv
import json
import os
import re
import threading
import time
from urllib.parse import quote

from django.conf import settings

_lock = threading.RLock()


def _rows_from_matrix(matrix):
    if not matrix:
        return []
    header = [str(h).strip().lower() for h in matrix[0]]
    out = []
    for i, row in enumerate(matrix[1:], start=2):
        d = {h: (str(row[j]).strip() if j < len(row) else "") for j, h in enumerate(header)}
        d["_row"] = i
        out.append(d)
    return out


class Tab:
    """Subclasses provide matrix(), write(row, rows) and clear()."""

    def read_rows(self):
        with _lock:
            return _rows_from_matrix(self.matrix())

    def ensure_headers(self, headers):
        with _lock:
            if not self.matrix():
                self.write(1, [list(headers)])

    def append(self, rows):
        if not rows:
            return
        with _lock:
            self.write(len(self.matrix()) + 1, [list(r) for r in rows])

    def reset(self, headers):
        with _lock:
            self.clear()
            self.write(1, [list(headers)])

    def expand(self, row, ident, names, resources=None):
        return row

    def add_rows(self, row, ident, names, resource="", color=None, values=None):
        return row

    def update_row(self, row, values, ident=None, must_be_free=False, color=None):
        """values: {"resource": "...", "status": "..."} (lower-case header names). Returns the row written."""
        with _lock:
            m = self.matrix() or [[]]
            header = [str(h).strip().lower() for h in m[0]]
            head_row, changed = list(m[0]), False
            for col in values:
                if col not in header:
                    header.append(col)
                    head_row.append(col.title())
                    changed = True
            if changed:
                self.write(1, [head_row])
            while len(m) < row:
                m.append([])
            r = list(m[row - 1]) + [""] * (len(header) - len(m[row - 1]))
            for col, val in values.items():
                r[header.index(col)] = val
            self.write(row, [r])
            return row


# ------------------------------------------------------------------ local CSV
class CsvTab(Tab):
    def __init__(self, path):
        self.path = path

    def matrix(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.reader(f))

    def _save(self, m):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(m)

    def write(self, row, rows):
        m = self.matrix()
        while len(m) < row - 1 + len(rows):
            m.append([])
        for i, r in enumerate(rows):
            m[row - 1 + i] = list(r)
        self._save(m)

    def clear(self):
        self._save([])


class CsvBook:
    def tab(self, name, headers=None):
        t = CsvTab(os.path.join(settings.SHEET_TABS_DIR, re.sub(r"[^\w\- ]", "_", name) + ".csv"))
        if headers:
            t.ensure_headers(headers)
        return t


# ------------------------------------------------------------------ Excel via Microsoft Graph
from .ms_auth import NotConnected as NotConnectedError  # noqa: E402


class GraphError(Exception):
    def __init__(self, status, text):
        super().__init__(f"Microsoft Graph {status}: {text}")
        self.status = status


_tok, _item_cache, _ready = {"v": None, "exp": 0}, {}, set()


_hdr_cache = {}


def reset_cache():
    _hdr_cache.clear()
    _sessions.clear()
    _tok.update(v=None, exp=0)
    _item_cache.clear()
    _ready.clear()


def excel_enabled():
    """Tenant id + client id + workbook link are enough. A client secret is optional: without it the admin
    signs in once from the dashboard (device code) and the token lives in the database."""
    return bool(settings.GRAPH_TENANT_ID and settings.GRAPH_CLIENT_ID
                and (settings.GRAPH_FILE_URL or (settings.GRAPH_DRIVE_ID and settings.GRAPH_ITEM_ID)))


def _token(force=False):
    if not settings.GRAPH_CLIENT_SECRET:                       # delegated: token kept in the database
        from . import ms_auth
        return ms_auth.get_token(force=force)
    if not force and _tok["v"] and _tok["exp"] > time.time() + 60:
        return _tok["v"]
    import requests
    r = requests.post(f"https://login.microsoftonline.com/{settings.GRAPH_TENANT_ID}/oauth2/v2.0/token", data={
        "client_id": settings.GRAPH_CLIENT_ID, "client_secret": settings.GRAPH_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}, timeout=30)
    if r.status_code >= 400:
        raise GraphError(r.status_code, "could not get a token (check tenant/client id and secret): " + r.text[:300])
    j = r.json()
    _tok.update(v=j["access_token"], exp=time.time() + int(j.get("expires_in", 3000)))
    return _tok["v"]


_sessions = {}
RETRY_STATUS = {408, 409, 423, 429, 500, 502, 503, 504}


def _workbook_base(url):
    m = re.match(r"(/drives/[^/]+/items/[^/]+)/workbook", url)
    return m.group(1) if m else None


def _session(base):
    """A workbook session makes reads see our own latest writes (no stale rows), like captureautomate does."""
    s = _sessions.get(base)
    if s and time.time() - s[1] < 240:
        s[1] = time.time()
        return s[0]
    try:
        j = _call("POST", f"{base}/workbook/createSession", {"persistChanges": True}, session=False)
        _sessions[base] = [j.get("id"), time.time()]
        return j.get("id")
    except NotConnectedError:
        raise
    except Exception:  # noqa: BLE001 - sessions are an optimisation only
        _sessions.pop(base, None)
        return None


def _call(method, url, body=None, session=True):
    import requests
    full = url if url.startswith("http") else "https://graph.microsoft.com/v1.0" + url
    base = _workbook_base(url) if session else None
    last, force = None, False
    for attempt in range(6):
        headers = {"Authorization": "Bearer " + _token(force)}
        force = False
        sid = _session(base) if base else None
        if sid:
            headers["workbook-session-id"] = sid
        try:
            r = requests.request(method, full, json=body, timeout=60, headers=headers)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            time.sleep(min(2 ** attempt, 20))
            continue
        if r.status_code == 401:                                   # token expired: get a fresh one and retry
            last, force = GraphError(401, r.text[:300]), True
            continue
        if r.status_code in RETRY_STATUS:                          # throttled / file locked / busy
            last = GraphError(r.status_code, r.text[:300])
            time.sleep(min(int(r.headers.get("Retry-After", min(2 ** attempt, 20))), 30))
            continue
        if r.status_code >= 400:
            if base and r.status_code in (400, 404) and "session" in r.text.lower():
                _sessions.pop(base, None)                          # expired session: a new one is made next time
                last = GraphError(r.status_code, r.text[:300])
                continue
            raise GraphError(r.status_code, r.text[:300])
        return r.json() if r.content else {}
    raise last if last else GraphError(0, "Graph call failed")


def runtime():
    """Today's sheet settings: typed in the admin panel (database) with .env as fallback."""
    from .models import AppState
    st = AppState.objects.filter(pk=1).first()
    start = st.start_row if st and st.start_row else 0
    return {"url": (st and st.captures_url) or settings.GRAPH_CAPTURES_URL or settings.GRAPH_FILE_URL,
            "tab": (st and st.captures_tab) or settings.CAPTURES_TAB,
            "start_row": start or int(settings.CAPTURE_START_ROW or 0),
            "explicit_start": bool(start), "end_row": (st.end_row if st else None)}


def _item(url=None):
    """'/drives/{drive}/items/{item}' of a workbook, from ids or from a pasted share link."""
    url = url or settings.GRAPH_FILE_URL
    if url not in _item_cache:
        d, i = settings.GRAPH_DRIVE_ID, settings.GRAPH_ITEM_ID
        if not (d and i and url == settings.GRAPH_FILE_URL):
            enc = "u!" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
            j = _call("GET", f"/shares/{enc}/driveItem?$select=id,parentReference")
            d, i = j["parentReference"]["driveId"], j["id"]
        _item_cache[url] = f"/drives/{d}/items/{i}"
    return _item_cache[url]


def _col(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xl_date(v):
    """Excel stores dates as numbers (45936.5): show them as text."""
    try:
        from datetime import datetime, timedelta
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return _s(v).strip()


def _s(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return "" if v is None else str(v)


class ExcelTab(Tab):
    def __init__(self, name):
        self.base = f"{_item()}/workbook/worksheets/{quote(name, safe='')}"

    def matrix(self):
        vals = _call("GET", f"{self.base}/usedRange(valuesOnly=true)").get("values", [])
        rows = [[_s(c) for c in r] for r in vals]
        while rows and not any(rows[-1]):
            rows.pop()
        return rows

    def write(self, row, rows):
        w = max(len(r) for r in rows)
        rows = [list(r) + [""] * (w - len(r)) for r in rows]
        url = f"{self.base}/range(address='A{row}:{_col(w)}{row + len(rows) - 1}')"
        # text format first, so Excel never turns "2026-10-19" or "007" into a date/number
        _call("PATCH", url, {"numberFormat": [["@" if isinstance(v, str) else "General" for v in r] for r in rows]})
        _call("PATCH", url, {"values": rows})

    def clear(self):
        _call("POST", f"{self.base}/usedRange/clear", {"applyTo": "All"})


class ExcelCaptureTab:
    """The team's real production tab (25,000+ rows, colours, formulas, filters).

    It is never reshaped by bulk writes: we read a window of rows and change single cells, colours
    and (when a capture is split) insert rows under the capture row. Before any write the row is
    re-read and checked (project / structure / job id still match, Resource still empty), because
    other people edit the same workbook and rows can move.
    """

    def __init__(self, name, url=None):
        self.name, self.url = name, url
        self.base = f"{_item(url)}/workbook/worksheets/{quote(name, safe='')}"
        self._key = f"{url}|{name}"

    # ---- columns
    def _headers(self):
        h = _hdr_cache.get(self._key)
        if not h or h[0] < time.time() - 600:
            vals = _call("GET", f"{self.base}/range(address='A1:BZ1')?$select=values").get("values", [[]])[0]
            h = (time.time(), {str(v).strip().lower(): i + 1 for i, v in enumerate(vals) if str(v).strip()})
            _hdr_cache[self._key] = h
        return h[1]

    def _name_of(self, key):
        return {"project": settings.CAPTURE_COL_PROJECT, "capture": settings.CAPTURE_COL_CAPTURE,
                "priority": settings.CAPTURE_COL_PRIORITY, "resource": settings.CAPTURE_COL_RESOURCE,
                "status": settings.CAPTURE_COL_STATUS, "ref": settings.CAPTURE_COL_REF,
                "path": settings.CAPTURE_COL_PATH, "date": settings.CAPTURE_COL_DATE,
                "remarks": settings.CAPTURE_COL_REMARKS}.get(key, key)

    def col(self, key):
        """Column number for a configured header. 'Structure' also finds 'Structure Name' (captureautomate's header)."""
        n = (self._name_of(key) or "").strip().lower()
        if not n:
            return None
        h = self._headers()
        if n in h:
            return h[n]
        for name, c in h.items():
            if name.startswith(n):
                return c
        return None

    # ---- reading (window only)
    def last_row(self):
        addr = _call("GET", f"{self.base}/usedRange(valuesOnly=true)?$select=address")["address"]
        return int(re.findall(r"\d+", addr.split("!")[-1])[-1])

    def window(self):
        rt, last = runtime(), self.last_row()
        if rt["explicit_start"]:                    # the admin typed a start row for today
            return max(2, rt["start_row"]), min(rt["end_row"] or last, last)
        return max(2, int(settings.CAPTURE_START_ROW or 0), last - int(settings.CAPTURE_LOOKBACK_ROWS) + 1), last

    def _scan(self, only_free):
        keys = ("project", "capture", "resource", "ref", "path", "date", "priority", "status")
        c = {k: self.col(k) for k in keys}
        if not (c["project"] and c["capture"] and c["resource"]):
            raise RuntimeError(f"Could not find the Project / Capture / Resource columns in tab '{self.name}'. "
                               "Check the CAPTURE_COL_* settings (run: python manage.py check_sheet).")
        lo, hi = min(v for v in c.values() if v), max(v for v in c.values() if v)
        start, end = self.window()
        out = []
        for a in range(start, end + 1, 2000):
            b = min(a + 1999, end)
            vals = _call("GET", f"{self.base}/range(address='{_col(lo)}{a}:{_col(hi)}{b}')?$select=values").get("values", [])
            for i, row in enumerate(vals):
                d = {k: (_s(row[c[k] - lo]).strip() if c[k] and 0 <= c[k] - lo < len(row) else "") for k in keys}
                d["_row"] = a + i
                if c["date"] and c["date"] - lo < len(row):
                    d["date"] = _xl_date(row[c["date"] - lo])
                d["job"], d["ref"] = d["ref"], d["ref"] or d["path"]     # identity: JOB ID, else the capture path
                if not d["project"] or not d["capture"]:
                    continue
                if only_free and d["resource"]:
                    continue
                out.append(d)
        return out

    def read_rows(self):
        """Only unassigned rows (Resource empty) inside the window count as new captures."""
        return self._scan(True)

    def describe(self):
        start, end = self.window()
        return {"headers": self._headers(), "start": start, "last": end,
                "cols": {k: self.col(k) for k in ("project", "capture", "ref", "path", "date", "priority", "resource", "status", "remarks")}}

    # ---- finding the right row again (rows move when people insert/delete rows)
    def _find(self, ident, must_be_free, want):
        ident = tuple(str(x).strip() for x in ident)
        for r in self._scan(False):
            if (r["project"], r["capture"], r["ref"]) == ident and (
                    not must_be_free or not r["resource"] or r["resource"] == want):
                return r["_row"]
        return None

    def locate(self, row, ident=None, must_be_free=False, want="", extra_cols=()):
        """-> (row, getter) with the verified row, or (None, None) if it is gone / already taken."""
        res = self.col("resource")
        idc = [self.col("project"), self.col("capture"), self.col("ref")]
        pth = self.col("path")
        cols = [c for c in idc + [pth, res] + list(extra_cols) if c]
        if not cols:
            return row, (lambda c: "")
        lo, hi = min(cols), max(cols)

        def snap(r):
            vals = _call("GET", f"{self.base}/range(address='{_col(lo)}{r}:{_col(hi)}{r}')?$select=values").get("values", [[]])[0]
            return lambda c: _s(vals[c - lo]).strip() if c and 0 <= c - lo < len(vals) else ""

        g = snap(row)
        ident_of = lambda g: (g(idc[0]), g(idc[1]), g(idc[2]) or g(pth))
        if ident and ident_of(g) != tuple(str(x).strip() for x in ident):
            row = self._find(ident, must_be_free, want)
            if row is None:
                return None, None
            g = snap(row)
        if must_be_free and res and g(res) and g(res) != want:
            return None, None                              # somebody already filled the Resource cell
        return row, g

    # ---- writing
    def _cell(self, col, row):
        return f"{self.base}/range(address='{_col(col)}{row}')"

    def _fill(self, r1, r2, color):
        last = max(self._headers().values() or [5])
        url = f"{self.base}/range(address='A{r1}:{_col(last)}{r2}')/format/fill"
        if color == "clear":
            _call("POST", url + "/clear")
        else:
            _call("PATCH", url, {"color": color})

    def get_fill(self, row):
        """Current fill colour of a row ('none' when it has no fill), so it can be restored later."""
        try:
            col = (_call("GET", f"{self.base}/range(address='A{row}')/format/fill").get("color") or "").strip()
        except Exception:  # noqa: BLE001
            return ""
        return "none" if col.upper() in ("", "#FFFFFF", "NONE") else col

    def update_row(self, row, values, ident=None, must_be_free=False, color=None):
        targets = {}
        for k in values:
            col = self.col(k) if k in ("resource", "status", "remarks") else self._headers().get(k.strip().lower())
            if col:
                targets[k] = col
        want = _s(values.get("resource", "")).strip()
        row, g = self.locate(row, ident, must_be_free, want, extra_cols=targets.values())
        if row is None:
            return None
        for k, col in targets.items():
            if g(col) != _s(values[k]).strip():
                _call("PATCH", self._cell(col, row), {"values": [[values[k]]]})
        if color:
            self._fill(row, row, color)
        return row

    def _block(self, row, names, resource_for, extra=None):
        """Values for new rows (ref, project, structure, resource, extras) as one rectangular block."""
        c = {k: self.col(k) for k in ("project", "capture", "ref", "path", "resource", "remarks")}
        cols = [v for v in c.values() if v]
        lo, hi = min(cols), max(cols)
        return c, lo, hi

    def _insert_rows(self, row, g, names, resources, extra=None, color=None):
        n = len(names)
        if n < 1:
            return
        c, lo, hi = self._block(row, names, resources, extra)
        _call("POST", f"{self.base}/range(address='{row + 1}:{row + n}')/insert", {"shift": "Down"})
        block = []
        for nm in names:
            r = [""] * (hi - lo + 1)
            for k in ("ref", "path"):                      # keep the identity of the capture on every split row
                if c[k]:
                    r[c[k] - lo] = g(c[k])
            r[c["project"] - lo] = g(c["project"])
            r[c["capture"] - lo] = nm
            if c["resource"]:
                r[c["resource"] - lo] = resources.get(nm, "")
            if extra and c["remarks"] and "remarks" in extra:
                r[c["remarks"] - lo] = extra["remarks"]
            block.append(r)
        _call("PATCH", f"{self.base}/range(address='{_col(lo)}{row + 1}:{_col(hi)}{row + n}')", {"values": block})
        if color:
            self._fill(row + 1, row + n, color)

    def expand(self, row, ident, names, resources=None):
        """One Level row -> len(names) rows: the row is renamed to names[0], the others go under it.
        Returns the row, or None if somebody already filled its Resource cell."""
        resources = resources or {}
        first = resources.get(names[0], "")
        res, rem = self.col("resource"), self.col("remarks")
        row, g = self.locate(row, ident, must_be_free=True, want=first, extra_cols=[self.col("remarks")])
        if row is None:
            return None
        _call("PATCH", self._cell(self.col("capture"), row), {"values": [[names[0]]]})
        if first and res:
            _call("PATCH", self._cell(res, row), {"values": [[first]]})
        self._insert_rows(row, g, names[1:], resources)
        return row

    def add_rows(self, row, ident, names, resource="", color=None, values=None):
        """Insert rows (sub-links) right under an existing row. Returns the row, or None if not found."""
        row, g = self.locate(row, ident, extra_cols=[self.col("remarks")])
        if row is None:
            return None
        self._insert_rows(row, g, names, {n: resource for n in names}, values, color)
        return row


class ExcelBook:
    def tab(self, name, headers=None):
        if name not in _ready:
            try:
                _call("GET", f"{_item()}/workbook/worksheets/{quote(name, safe='')}")
            except GraphError as e:
                if e.status != 404:
                    raise
                _call("POST", f"{_item()}/workbook/worksheets/add", {"name": name})
            if headers:
                ExcelTab(name).ensure_headers(headers)
            _ready.add(name)
        return ExcelTab(name)


# ------------------------------------------------------------------ Google (legacy: Captures tab only)
class GoogleSheet:
    def __init__(self):
        import gspread

        if settings.GOOGLE_CREDENTIALS_JSON:
            gc = gspread.service_account_from_dict(json.loads(settings.GOOGLE_CREDENTIALS_JSON))
        else:
            gc = gspread.service_account(filename=settings.GOOGLE_CREDENTIALS_FILE)
        book = gc.open_by_key(settings.GOOGLE_SHEET_ID)
        self.ws = book.worksheet(settings.GOOGLE_WORKSHEET) if settings.GOOGLE_WORKSHEET else book.sheet1

    def read_rows(self):
        return _rows_from_matrix(self.ws.get_all_values())

    def update_row(self, row, values, ident=None, must_be_free=False, color=None):
        import gspread

        header = [h.strip().lower() for h in self.ws.row_values(1)]
        cells = []
        for col in values:
            if col not in header:
                header.append(col)
                cells.append(gspread.Cell(1, len(header), col.title()))
        for col, val in values.items():
            cells.append(gspread.Cell(row, header.index(col) + 1, val))
        self.ws.update_cells(cells)
        return row


# ------------------------------------------------------------------ entry points
def get_book():
    """Multi-tab workbook (Users, Today, monthly logs, Shift Summary). None for the legacy Google mode."""
    if excel_enabled():
        return ExcelBook()
    if settings.GOOGLE_SHEET_ID:
        return None
    return CsvBook()


def get_sheet():
    """The Captures tab (what the app reads work from)."""
    if excel_enabled():
        rt = runtime()
        return ExcelCaptureTab(rt["tab"], rt["url"])
    if settings.GOOGLE_SHEET_ID:
        return GoogleSheet()
    return CsvTab(settings.SHEET_CSV_PATH)
