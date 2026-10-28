# CaptureFlow

**CaptureFlow** is a daily capture-work allocation and shift-tracking platform built with **Django REST Framework** and **React**. It imports capture records from spreadsheets, distributes work across the active roster, tracks progress at split level, and writes operational updates and shift history back to Microsoft Excel or Google Sheets.

> **Workflow:** `Assigned → Picked → Working → Completed`

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Workflow](#workflow)
- [Technology Stack](#technology-stack)
- [Architecture](#architecture)
- [Data and Shift Management](#data-and-shift-management)
- [Spreadsheet Integrations](#spreadsheet-integrations)
- [Getting Started](#getting-started)
- [Environment Configuration](#environment-configuration)
- [API and Management Commands](#api-and-management-commands)
- [Deployment](#deployment)
- [Security Notes](#security-notes)
- [Testing](#testing)
- [Project Structure](#project-structure)

## Overview

CaptureFlow helps operations teams coordinate daily capture-processing work. Administrators manage the daily roster and workload, while resources pick up assigned captures, process individual splits, and record completion. The application supports priority-based allocation, split handovers, shift summaries, and spreadsheet-backed operational records.

## Key Features

- **Spreadsheet import and synchronization** — import capture records from a connected workbook or Google Sheet.
- **Priority-based assignment** — distribute waiting work by priority while considering each team member's current load.
- **Split-level tracking** — record work started and completed for each split.
- **Overflow handover** — when a capture contains more than five splits, retain all splits or hand over the overflow in batches of up to five.
- **Self-service task queue** — resources can request another task, with handed-over work offered before other waiting work.
- **Live operational dashboard** — view team assignments, task stages, timings, statistics, and searchable capture history.
- **Daily roster management** — configure the people working a shift and create resource accounts as needed.
- **Shift summaries and monthly logs** — retain per-resource shift metrics and completed-split history in the workbook.
- **Daily workspace reset** — clear day-specific operational data at the configured shift boundary while preserving admin accounts.
- **Deployment options** — React frontend deployment on Vercel or Netlify and Django backend deployment on Render, with an external PostgreSQL database.

## Workflow

1. **Import captures:** An administrator starts a sheet sync, or a scheduled request triggers the sync endpoint.
2. **Configure the roster:** The administrator sets the resources working that day.
3. **Allocate work:** Waiting captures are assigned according to priority and current workload.
4. **Pick a capture:** The resource records the pick time and enters the number of splits.
5. **Process splits:** Each split is marked as started and then completed.
6. **Handle overflow:** Splits beyond the configured five-split threshold can be retained or handed over to teammates.
7. **Complete the capture:** Once all splits are done, the capture is marked completed and its processing time is recorded.
8. **Close the shift:** The application writes shift summaries and completed-split history before clearing daily operational data.

## Technology Stack

| Layer | Technologies |
|---|---|
| Backend | Python, Django, Django REST Framework |
| Frontend | React, Vite |
| Authentication | Token authentication; Microsoft sign-in for supported workbook integration |
| Local database | SQLite |
| Production database | PostgreSQL |
| Spreadsheet integration | Google Sheets API and/or Microsoft Graph, depending on configuration |
| Deployment | Render, Vercel or Netlify |
| Scheduled synchronization | External scheduler such as cron-job.org |

## Architecture

```text
Google Sheets / Microsoft Excel (SharePoint or OneDrive)
                         |
                         v
                 Django REST API
                         |
             +-----------+-----------+
             |                       |
             v                       v
        PostgreSQL                Sync jobs
      (production data)        / scheduled requests
             |
             v
       React + Vite UI
       Admin and Resource views
```

The exact spreadsheet integration depends on the configuration used by your deployment. The application can also use a sample CSV for local testing when Google Sheets credentials are not configured.

## Data and Shift Management

### Daily operations

The application maintains day-specific records such as captures, splits, roster assignments, and notifications. A new day can be started manually from the dashboard or triggered automatically at the configured day boundary.

- The default day boundary is midnight.
- Set `DAY_START_HOUR` to use a different boundary; for example, `7` for a shift that rolls over at 7:00 AM.
- Admin accounts are preserved during the daily reset.
- Set `KEEP_RESOURCE_ACCOUNTS=1` if resource accounts should survive the reset.

### Workbook records

Depending on the selected workbook setup, the control workbook can contain:

| Worksheet | Purpose |
|---|---|
| `Resources` / `Users` | User details, roles, account status, and roster settings |
| `Captures` | Capture input and processing status |
| `Today` | Current shift roster |
| `Shift Summary` | Per-resource shift metrics and team totals |
| `Night YYYY-MM` or `Daily Work YYYY-MM` | Monthly or daily processing history |

The production workbook can remain separate from the control workbook. For the existing production tab configuration, CaptureFlow can read new rows and update the **Resource** cell when assigning work. Confirm the selected configuration before enabling write access.

### Production sheet format

For the basic Google Sheets configuration, the header row should include:

```text
Project | Capture | Priority | Resource | Status
```

Only `Project` and `Capture` are required for basic import. The application can add or populate fields such as `Resource`, `Status`, `Splits`, `Picked At`, and `Completed At`.

For an existing production workbook, configure the worksheet and column names to match its actual headers. The `Detail Visual projects` configuration described in the project notes uses fields such as `Projects`, `Structure`, `JOB ID`, `Capture path`, `Date of Capture`, and `Resource`.

## Getting Started

### Prerequisites

Install the following before running the application locally:

- Python 3.11 or another version supported by the project's dependencies
- Node.js and npm
- Git
- A database supported by the selected configuration (SQLite works for local development)

### 1. Clone the repository

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd CaptureFlow
```

Replace `<YOUR_GITHUB_REPOSITORY_URL>` with your repository URL.

### 2. Configure and run the backend

```bash
cd backend

# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
# python -m venv .venv
# source .venv/bin/activate

pip install -r requirements.txt
```

Create the backend environment file using the variables required by your selected configuration. Then run:

```bash
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

The backend runs at `http://127.0.0.1:8000` by default.

`seed_demo` is intended for local development only. Do not use demo credentials in a production deployment.

### 3. Configure and run the frontend

Open a second terminal:

```bash
cd frontend
```

Create the local environment file from the example:

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Set the API URL in `.env` if required, then install dependencies and start Vite:

```bash
npm install
npm run dev
```

The frontend typically runs at `http://localhost:5173`.

### 4. Test without live spreadsheet credentials

When Google Sheets credentials are not configured, the application can use `backend/sample_sheet.csv` for offline testing, as described in the project configuration.

## Environment Configuration

Use the variables supported by your selected integration. The following are referenced in the project configuration; not every variable is required in every deployment.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string for production |
| `ADMIN_EMAIL` | Initial administrator email |
| `ADMIN_PASSWORD` | Initial administrator password |
| `CORS_ALLOWED_ORIGINS` | Allowed frontend origin(s); use the deployed frontend URL without a trailing slash |
| `GOOGLE_SHEET_ID` | Google Sheet identifier |
| `GOOGLE_CREDENTIALS_JSON` | Google service-account JSON credentials |
| `GOOGLE_WORKSHEET` | Optional Google worksheet/tab name |
| `GRAPH_TENANT_ID` | Microsoft Entra tenant ID |
| `GRAPH_CLIENT_ID` | Microsoft Entra application/client ID |
| `GRAPH_FILE_URL` | Link to the control workbook |
| `DAY_START_HOUR` | Hour at which the application starts a new operational day |
| `KEEP_RESOURCE_ACCOUNTS` | Set to `1` to preserve resource accounts across daily resets |
| `AUTO_SYNC_SECONDS` | Dashboard sheet-sync interval, when enabled by the selected configuration |
| `CRON_SECRET` | Secret used to protect the scheduled sync endpoint |
| `CAPTURE_LOOKBACK_ROWS` | Number of recent rows checked when using the existing production-tab integration |
| `SPLIT_ROWS_MODE` | Controls split-row insertion behavior in the production workbook |

**Important:** Keep credentials in environment variables or a secret manager. Never commit `.env` files, service-account keys, Microsoft client secrets, access tokens, or production passwords.

## Spreadsheet Integrations

### Google Sheets

1. Create a project in Google Cloud Console.
2. Enable the Google Sheets API.
3. Create a service account and generate its JSON key.
4. Share the target sheet with the service account's `client_email`, granting Editor access if the app must write updates.
5. Configure `GOOGLE_SHEET_ID` and `GOOGLE_CREDENTIALS_JSON`; set `GOOGLE_WORKSHEET` if needed.

### Microsoft Excel on OneDrive or SharePoint

The Microsoft Graph integration can work with a workbook stored in OneDrive for Business or SharePoint.

1. Register an application in Microsoft Entra ID.
2. Configure the appropriate Microsoft Graph permissions for the authentication mode used by the application.
3. Grant the signed-in user or application the required workbook access.
4. Configure the relevant `GRAPH_*` environment variables and workbook link.
5. Use the application's Microsoft sign-in flow if the configured mode uses delegated authentication.

The project notes describe both an application-permission setup and a delegated sign-in setup. These are different authentication models; follow the implementation and environment example in your checked-out version rather than combining their settings. Use least-privilege permissions and obtain administrator consent where required.

For the existing production-tab workflow, run the sheet-check command before enabling writes:

```bash
python manage.py check_sheet
```

This is intended as a dry run to report detected columns and the number of rows that would be imported.

## API and Management Commands

The exact API routes depend on the backend implementation. The project notes identify the following operations:

| Operation | Purpose |
|---|---|
| `POST` or configured request to `/api/cron/sync/` | Trigger scheduled spreadsheet synchronization; protect it with `CRON_SECRET` |
| `python manage.py migrate` | Apply database migrations |
| `python manage.py seed_demo` | Create local demo data/accounts |
| `python manage.py create_resources people.csv` | Bulk-create resource accounts from a CSV |
| `python manage.py setup_sheet` | Create the configured workbook worksheets |
| `python manage.py check_sheet` | Dry-run validation of the production sheet integration |
| `python manage.py check_env` | Display environment configuration checks |
| `python manage.py check_env --test` | Test workbook access in read-only mode |
| `python manage.py ms_login` | Start Microsoft sign-in from a terminal, where supported |

Check the Django URL configuration and command help in your current checkout for the authoritative HTTP methods, request payloads, and available commands.

## Deployment

### Backend — Render

1. Push the repository to GitHub.
2. In Render, create a new Blueprint from the repository if the project includes a valid `render.yaml`.
3. Configure the required environment variables.
4. Use a managed PostgreSQL database from a provider such as Neon or Supabase, or another compatible PostgreSQL service.
5. Deploy and review the build and runtime logs.

**Free-tier considerations:** Render free web services may spin down after inactivity, causing a delay on the next request. Free-tier limits and provider policies can change. Verify current database retention, usage limits, and sleep behavior before relying on a free deployment for operational workloads.

### Frontend — Vercel or Netlify

1. Import the GitHub repository.
2. Set the frontend root directory to `frontend`.
3. Set the build command to `npm run build`.
4. Set the output directory to `dist`.
5. Configure `VITE_API_URL` to the deployed backend URL, for example:

   ```text
   VITE_API_URL=https://<your-service>.onrender.com
   ```

6. Deploy the frontend.
7. Add the final frontend URL to the backend's `CORS_ALLOWED_ORIGINS` and redeploy the backend if needed.

### Scheduled synchronization

If synchronization must run when nobody has the dashboard open, configure a scheduler such as cron-job.org to call the protected sync endpoint at the required interval. Store the secret securely and verify the endpoint's authentication and expected HTTP method before enabling the schedule.

## Security Notes

- Use strong, unique production credentials and rotate them if exposed.
- Do not commit secrets, workbook links containing sensitive tokens, or credential files.
- Restrict CORS to trusted frontend origins.
- Protect scheduled synchronization endpoints with a secret or equivalent authentication.
- Grant only the spreadsheet permissions the application needs.
- Use HTTPS for deployed frontend, API, and authentication redirects.
- Confirm daily-reset and workbook-write behavior with test data before using production workbooks.
- Treat demo accounts and sample data as development-only.

## Testing

Run the Django test suite from the backend directory:

```bash
python manage.py test
```

Before connecting a production workbook, validate environment settings and use the available read-only/dry-run checks.

## Project Structure

The repository is expected to contain a backend and frontend similar to the following. Adjust this outline to match the actual files in your repository.

```text
CaptureFlow/
├── backend/
│   ├── manage.py
│   ├── requirements.txt
│   └── ...
├── frontend/
│   ├── package.json
│   ├── .env.example
│   └── ...
├── render.yaml
└── README.md
```

## Contributing

1. Create a feature branch.
2. Keep changes focused and add or update tests where appropriate.
3. Verify backend tests and frontend production builds.
4. Open a pull request describing the change and any configuration updates.

## License

No license has been specified yet. Add a `LICENSE` file before distributing or accepting external contributions. Until then, do not assume the project is open source or that others have permission to reuse it.
