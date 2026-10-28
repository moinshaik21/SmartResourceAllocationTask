import os
import sys
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")   # reads backend/.env when present


def env(key, default=None):
    return os.environ.get(key, default)


def env_list(key, default=""):
    return [x.strip() for x in env(key, default).split(",") if x.strip()]


SECRET_KEY = env("SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = env("DEBUG", "1") == "1"
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "*")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "core.middleware.DailyResetMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=300,
        conn_health_checks=True,  # re-connects if Neon paused the DB while idle
    )
}

AUTH_USER_MODEL = "core.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 6}},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "EXCEPTION_HANDLER": "core.views.exception_handler",
}

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")
if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True

# ---- Business rules -------------------------------------------------------
MAX_SPLITS_PER_PERSON = int(env("MAX_SPLITS_PER_PERSON", "5"))

# ---- Daily reset ----------------------------------------------------------
# The database only holds the CURRENT working day. At the first request of a new
# day everything (projects, captures, splits, roster, notifications and, by default,
# resource accounts) is wiped. The Google Sheet is the permanent record.
DAY_START_HOUR = int(env("DAY_START_HOUR", "0"))              # 0 = reset at midnight, 6 = at 6 am
KEEP_RESOURCE_ACCOUNTS = env("KEEP_RESOURCE_ACCOUNTS", "0") == "1"

# ---- Google Sheet (leave GOOGLE_SHEET_ID empty to use the local CSV demo sheet)
GOOGLE_SHEET_ID = env("GOOGLE_SHEET_ID", "")
GOOGLE_WORKSHEET = env("GOOGLE_WORKSHEET", "")            # blank = first tab
GOOGLE_CREDENTIALS_JSON = env("GOOGLE_CREDENTIALS_JSON", "")  # service-account JSON (string)
GOOGLE_CREDENTIALS_FILE = env("GOOGLE_CREDENTIALS_FILE", "")  # or a path to the JSON file
# ---- Excel on OneDrive/SharePoint via Microsoft Entra app (Microsoft Graph) -------------
GRAPH_TENANT_ID = env("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID = env("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET = env("GRAPH_CLIENT_SECRET", "")
GRAPH_FILE_URL = env("GRAPH_FILE_URL", "")      # the "Copy link" URL of the .xlsx
GRAPH_DRIVE_ID = env("GRAPH_DRIVE_ID", "")      # optional: skips the URL lookup
GRAPH_ITEM_ID = env("GRAPH_ITEM_ID", "")
CAPTURES_TAB = env("CAPTURES_TAB", "Captures")
# Excel mode needs only tenant id + client id + workbook link. With a client secret the app signs in by itself;
# without one the admin signs in ONCE from the dashboard (device code, like captureautomate) and the token is kept in the DB.
_excel = bool(GRAPH_TENANT_ID and GRAPH_CLIENT_ID and (GRAPH_FILE_URL or GRAPH_ITEM_ID))
# 1 = logins/roster come from the workbook's Users tab (default when Excel is configured)
USERS_FROM_SHEET = env("USERS_FROM_SHEET", "1" if _excel else "0") == "1"
# Column names of YOUR capture tab (header row 1). Leave a name empty if the column does not exist.
CAPTURE_COL_PROJECT = env("CAPTURE_COL_PROJECT", "Project")
CAPTURE_COL_CAPTURE = env("CAPTURE_COL_CAPTURE", "Capture")
CAPTURE_COL_REF = env("CAPTURE_COL_REF", "")
CAPTURE_COL_PATH = env("CAPTURE_COL_PATH", "Capture path")       # written by captureautomate (V4), column H
CAPTURE_COL_DATE = env("CAPTURE_COL_DATE", "Date of Capture")    # column F              # e.g. "JOB ID" (tells apart repeated project/structure names)
CAPTURE_COL_PRIORITY = env("CAPTURE_COL_PRIORITY", "Priority")
CAPTURE_COL_RESOURCE = env("CAPTURE_COL_RESOURCE", "Resource")
CAPTURE_COL_STATUS = env("CAPTURE_COL_STATUS", "Status")
# Big production sheet: only the last N rows are scanned for new work (and/or start at a fixed row)
CAPTURE_LOOKBACK_ROWS = int(env("CAPTURE_LOOKBACK_ROWS", "3000"))
CAPTURE_START_ROW = int(env("CAPTURE_START_ROW", "0"))
# What the app may write into the capture tab. With Excel only the Resource cell is touched by default.
CAPTURE_WRITEBACK = [x.strip().lower() for x in env(
    "CAPTURE_WRITEBACK", "resource" if _excel else "resource,status,splits,picked at,completed at").split(",") if x.strip()]
# At shift end, clear the Resource cell of unfinished captures so they come back as new work
CAPTURE_RELEASE_UNFINISHED = env("CAPTURE_RELEASE_UNFINISHED", "1" if _excel else "0") == "1"
LOG_TAB_PREFIX = env("LOG_TAB_PREFIX", "")                 # e.g. "Night " -> tabs "Night 2026-10"
USERS_TAB = env("USERS_TAB", "Resources")
PIPELINES_TAB = env("PIPELINES_TAB", "Pipelines")
GRAPH_CAPTURES_URL = env("GRAPH_CAPTURES_URL", "")   # production workbook (can also be typed in the admin panel)
CAPTURE_COL_REMARKS = env("CAPTURE_COL_REMARKS", "Remarks")
# insert = the Level row becomes one row per split (Level 05 sp-1 ...); none = keep one row
SPLIT_ROWS_MODE = env("SPLIT_ROWS_MODE", "insert" if _excel else "none")
SPLIT_NAME_FMT = env("SPLIT_NAME_FMT", "{base} sp-{n}")
NAME_ON_START = env("NAME_ON_START", "1" if _excel else "0") == "1"   # write the name only once work has started
COLOR_STARTED = env("COLOR_STARTED", "#FFFF00")
COLOR_DONE = env("COLOR_DONE", "#00B050")
AUTO_SYNC_SECONDS = 0 if "test" in sys.argv else int(env("AUTO_SYNC_SECONDS", "90"))   # how often new captures are pulled while the admin dashboard is open
SHEET_TABS_DIR = env("SHEET_TABS_DIR", str(BASE_DIR / "local_sheets"))   # tabs when running on local CSV files
SHEET_CSV_PATH = env("SHEET_CSV_PATH", str(BASE_DIR / "sample_sheet.csv"))
CRON_SECRET = env("CRON_SECRET", "")  # lets a free cron service call /api/cron/sync/

import sys as _sys
if "test" in _sys.argv:   # fast hashing so the test-suite runs in seconds
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
