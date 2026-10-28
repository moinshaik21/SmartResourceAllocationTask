# CaptureFlow

Reads new captures from a Google Sheet, assigns them to the people working today, tracks
**Assigned → Picked → Working → Completed** with timestamps, and handles split overflow.

**Stack:** Django + DRF (token auth) · React (Vite) · SQLite locally / Postgres in production

## How it works
| Step | What happens |
|---|---|
| Sheet → app | Admin clicks **Read sheet** (or a cron pings `/api/cron/sync/`). New projects and captures are imported, with `Priority` (High/Medium/Low). |
| Roster | Admin picks/pastes the emails working today and saves. Waiting captures are shared out by priority to the person with the lightest load. Each person gets a "You got a task" notification, and the **Resource** and **Status** columns in the sheet are updated. |
| Resource home | Sees assigned captures. No work? **I need a task** gives handed-over splits first, then the highest-priority waiting capture. |
| Picking | Resource presses **Pick** (time stored), then enters the number of splits. If it is **more than 5**, a dialog asks: keep all, or keep 5 and hand over the rest. Handed-over splits go (in chunks of 5) to today's least-loaded teammates. If nobody is free they wait in a shared queue that "I need a task" drains first. |
| Finishing | Each split is Started / Done. When every split is done the capture becomes **Completed** and the time taken is recorded. |
| Admin dashboard | Team board (who is on which capture, stage, times), live stats, searchable capture log, manual assign/reassign on the Projects page. |

## One working day at a time
The database holds **only the current working day**. At the first request after the day changes
(midnight by default, set `DAY_START_HOUR` for another time), projects, captures, splits, roster,
notifications and resource accounts are wiped automatically. Admin accounts are never touched.
Admins can also click **Start new day** on the dashboard.

The **Google Sheet is the permanent record**. The app writes Resource, Status, Splits, Picked At and
Completed At back to it. Rows marked *Completed* are skipped on import; unfinished rows from
yesterday come back into the queue as new work.

Each day the admin pastes that day's people (`name, email, password`, password optional) on
**Today's roster**; accounts are created instantly and generated passwords are shown once to share.
Set `KEEP_RESOURCE_ACCOUNTS=1` if you want accounts to survive the daily reset.

## Sheet format
Row 1 headers (any order, any case): `Project | Capture | Priority | Resource | Status`.
Only Project and Capture are required; the app adds and fills Resource, Status, Splits, Picked At and Completed At.

## Run locally
```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo          # demo logins (local only)
python manage.py runserver          # http://127.0.0.1:8000

# frontend (new terminal)
cd frontend
cp .env.example .env
npm install
npm run dev                         # http://localhost:5173
```
Demo logins: `admin@demo.com / admin123` and `asha@demo.com / pass1234` (also ravi, meena, kiran).
Without Google credentials the app uses `backend/sample_sheet.csv`, so you can test the whole flow offline.
Run tests with `python manage.py test`.

Real resources: add them on the **Resources** page, or bulk-load a CSV (`name,email,password`):
`python manage.py create_resources people.csv`

## Connect your real Google Sheet
1. Google Cloud Console: create a project, enable **Google Sheets API**, create a **service account**, add a JSON key.
2. Open your sheet, click Share, and add the service account's `client_email` as **Editor**.
3. Set env vars `GOOGLE_SHEET_ID` (the long id in the sheet URL) and `GOOGLE_CREDENTIALS_JSON` (paste the whole JSON). Optional: `GOOGLE_WORKSHEET` (tab name).

## Deploy for free
**Database:** create a free Postgres at neon.tech or Supabase and copy its connection string (Render's free Postgres expires after 30 days).

**Backend on Render (free web service)**
1. Push this repo to GitHub, then in Render choose New > Blueprint and pick the repo (it uses `render.yaml`).
2. Fill the prompted env vars: `DATABASE_URL`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `CORS_ALLOWED_ORIGINS` (your frontend URL, no trailing slash), and the Google vars.
3. Deploy. The build runs migrations and creates your admin automatically.

**Frontend on Vercel or Netlify (free)**
1. Import the repo, set root directory to `frontend`, build command `npm run build`, output `dist`.
2. Env var: `VITE_API_URL=https://<your-service>.onrender.com`
3. Redeploy, then put the final frontend URL into the backend's `CORS_ALLOWED_ORIGINS`.

**Free-tier notes**
- Render's free service sleeps after about 15 minutes idle (the first request then takes ~30 s). To pull new sheet rows and auto-assign without anyone logging in, create a free job on cron-job.org that calls `https://<api>/api/cron/sync/?key=<CRON_SECRET>` every 10 minutes. This also keeps the service awake.
- Notifications are in-app (polled every 8 s), so no email or websocket service is needed.


---
## Excel (Microsoft 365) backend, shift archive and monthly logs
The workbook (one .xlsx on OneDrive for Business / SharePoint) holds these tabs. `python manage.py setup_sheet` creates them.

| Tab | Purpose |
|---|---|
| `Users` | Name, Email, Password Hash, Role (admin/resource), Active (Y/N). Source of truth for logins and the roster. |
| `Captures` | Work input; the app writes Resource, Status, Splits, Picked At, Completed At back. |
| `Today` | Today's roster (rewritten when the admin saves the roster). |
| `2026-10`, `2026-11` ... | One row per finished split: Date, Resource Name, Email, Project, Capture, Level, Split Name, Done At. Created automatically each month. |
| `Shift Summary` | One row per person per shift: captures completed, splits done, splits handed over, first pick, last done, minutes. |

**Entra app setup:** create a client secret; add Microsoft Graph *Application* permission `Files.ReadWrite.All`
(or `Sites.Selected` for one site) and click *Grant admin consent*. Put tenant id, client id, secret and the
file's "Copy link" URL into `GRAPH_*` env vars.

**Shift end (7 pm-6 am shift, `DAY_START_HOUR=7`):** at the first request after 7 am (or when the admin clicks *Start new day*)
the app writes the Shift Summary, clears Today, **then** wipes the database. If Excel is unreachable nothing is wiped and it retries
after 5 minutes. Failed monthly-log writes are kept in an outbox table and retried before the wipe.


### Using your existing production tab (e.g. "Detail Visual projects")
* **New captures** = rows in the last `CAPTURE_LOOKBACK_ROWS` rows where **Resource is empty** and Project + Structure are filled.
* **Assigning** writes the person's name into that row's **Resource cell only**. Other columns, colours, formulas and formats are never touched.
  Before writing, the row is re-read: if a colleague already typed a name, or the row moved (rows inserted above), the app copes
  (skips the row / finds it again).
* **Shift end:** finished captures keep the name; unfinished ones get Resource cleared so they are new work next shift.
* Per-person history lives in the sub-sheets: `Night 2026-10` (one row per finished split) and `Shift Summary`
  (per person per shift + `Team Total Splits (Shift)`).
* `python manage.py check_sheet` is a safe dry run that shows the columns it found and how many new rows it would import.


---
## Two workbooks, daily routine, and what gets written where
* **Control workbook** (`GRAPH_FILE_URL`, permanent): tabs `Resources`, `Pipelines`, `Today`, `Night 2026-10` ..., `Shift Summary`. Create it once.
* **Production workbook** (your monthly "Techops Processing Status"): never gets extra tabs. Paste its link and the start row on the dashboard.
* `Resources`: Name, Email, New Password, Password Hash, Role (admin/resource/senior), Active, Working Today, Splits Today, Senior For.
  Type a first password in *New Password*: the app hashes it and erases the typed text. Put `Y` in *Working Today* for tonight's people.
* **Daily:** Dashboard -> "Start today's shift" (start row, Automatic or Manual) -> employees pick and start.
* **Production tab writes** (nothing is written while a task is only allocated or sent back):
  Start -> name in Resource (+ the Level row becomes `Level 05 sp-1 ... sp-n`), row turns yellow; Done -> green;
  wrap-up -> pipeline remark in the Remarks column and sub-link rows (`sp-1 link-1-1`, `sp-1 link-1-2`, `sp-2 link-2`) in green.
* **Transfers:** "I can't take this" (reason) -> admin inbox -> senior of that project -> they see it as a new task.
* `SPLIT_ROWS_MODE=none` keeps one row per capture (only colours it) if you do not want the app to insert rows.


---
## Connecting Excel the same way as captureautomate (V4): client id + tenant id + workbook links, one sign-in
1. `.env` needs only `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID` (the same values as `CLIENT_ID` / `TENANT_ID` in V4's watcher.py)
   and `GRAPH_FILE_URL` (the Control workbook link). No client secret.
2. Run `python manage.py migrate`, start the backend, open the admin dashboard and click **Sign in with Microsoft**:
   open the link, type the code, sign in. (Or from a terminal: `python manage.py ms_login`.)
3. The token (access + refresh) is stored **encrypted in the database** and refreshed silently. If it ever stops working
   (password change, very long inactivity) the dashboard shows "Microsoft sign-in expired" and the same button signs in again.
   Do not change `SECRET_KEY` on a running server: it encrypts that token (you would only need to sign in again).
4. The Entra app must allow public client flows (it already does for captureautomate) and have the delegated permission
   `Files.ReadWrite.All`. The workbooks are read/written as the person who signed in, so that person needs edit access.
5. Dashboard polls the sheet itself: with a start row typed, new captures written by captureautomate appear within
   `AUTO_SYNC_SECONDS` while the dashboard is open (in Automatic mode they are also allocated). "Read sheet" does it on demand.
6. `python manage.py check_env` shows what was loaded; `check_env --test` opens both workbooks (read-only).
7. Day-by-day record: `Shift Summary` (one row per person on duty each shift, with status) and `Daily Work 2026-10`
   (one row per person, one column per day = splits done).
