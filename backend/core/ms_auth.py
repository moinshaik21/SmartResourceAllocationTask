"""Microsoft sign-in without a client secret (same method as captureautomate / V4).

* The admin clicks "Sign in with Microsoft" on the dashboard: we start a DEVICE CODE flow, the dashboard shows
  the code, the admin signs in once at microsoft.com/devicelogin.
* MSAL's token cache (access + refresh token) is stored ENCRYPTED in the database (MsToken). Tokens are refreshed
  silently from then on. If the refresh token stops working (password change, long inactivity, consent removed)
  the status becomes "expired" and the dashboard asks the admin to sign in again.
"""
import base64
import hashlib
import logging
import threading
import time

import msal
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import connection
from django.utils import timezone

from .models import MsToken

log = logging.getLogger(__name__)
SCOPES = ["Files.ReadWrite.All"]            # delegated: the admin's own access to the workbooks
INTERACTION_ERRORS = {"invalid_grant", "interaction_required", "consent_required", "login_required"}


class NotConnected(RuntimeError):
    """Microsoft is not connected, or the sign-in expired - an admin must sign in again on the dashboard."""


_tok = {"v": None, "exp": 0.0}
_flow = {"state": "idle", "error": "", "expires_at": 0}


def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()))


def _app(cache):
    return msal.PublicClientApplication(
        settings.GRAPH_CLIENT_ID, authority=f"https://login.microsoftonline.com/{settings.GRAPH_TENANT_ID}",
        token_cache=cache)


def _load():
    cache = msal.SerializableTokenCache()
    row = MsToken.objects.filter(pk=1).first()
    if row and row.cache:
        try:
            cache.deserialize(_fernet().decrypt(row.cache.encode()).decode())
        except InvalidToken:
            _mark("expired", "The stored sign-in cannot be read any more (SECRET_KEY changed?)")
            raise NotConnected("The stored Microsoft sign-in cannot be read. An admin must sign in again.")
    return cache, row


def _save(cache, **fields):
    row, _ = MsToken.objects.get_or_create(pk=1)
    row.cache = _fernet().encrypt(cache.serialize().encode()).decode()
    for k, v in fields.items():
        setattr(row, k, v)
    row.save()
    return row


def _mark(status, error=""):
    row, _ = MsToken.objects.get_or_create(pk=1)
    row.status, row.last_error = status, error[:300]
    row.save(update_fields=["status", "last_error", "updated_at"])
    _tok.update(v=None, exp=0.0)


def get_token(force=False):
    """A valid access token for Graph. Raises NotConnected when an admin has to sign in again."""
    if not force and _tok["v"] and _tok["exp"] > time.time() + 120:
        return _tok["v"]
    cache, row = _load()
    if not row or not row.cache:
        raise NotConnected("Microsoft is not connected. An admin must sign in once from the dashboard.")
    app = _app(cache)
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0], force_refresh=force) if accounts else None
    if not result or result.get("error") in INTERACTION_ERRORS:
        _mark("expired", ((result or {}).get("error_description") or "Sign-in expired")[:300])
        raise NotConnected("The Microsoft sign-in expired. An admin must sign in again from the dashboard.")
    if "access_token" not in result:                     # network hiccup etc.: not an expiry
        raise RuntimeError(result.get("error_description") or "Could not get a Microsoft token")
    if cache.has_state_changed:
        _save(cache)
    _tok.update(v=result["access_token"], exp=time.time() + int(result.get("expires_in", 3000)))
    return _tok["v"]


# ---------------------------------------------------------------- sign in (device code)
def start_device_flow():
    cache = msal.SerializableTokenCache()
    app = _app(cache)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(flow.get("error_description") or "Could not start the Microsoft sign-in")
    _flow.update(state="waiting", error="", expires_at=flow.get("expires_at", time.time() + 900))
    threading.Thread(target=_wait, args=(app, cache, flow), daemon=True).start()
    return {"user_code": flow["user_code"], "verification_uri": flow["verification_uri"],
            "expires_in": flow.get("expires_in", 900), "message": flow.get("message", "")}


def _wait(app, cache, flow):
    """Runs in a background thread: MSAL polls Microsoft until the admin has signed in (or the code expires)."""
    try:
        result = app.acquire_token_by_device_flow(flow)
        complete(cache, result)
    except Exception as e:  # noqa: BLE001
        log.exception("Microsoft device sign-in failed")
        _flow.update(state="failed", error=str(e)[:300])
    finally:
        connection.close()


def complete(cache, result):
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description") or str(result)[:200])
    account = (result.get("id_token_claims") or {}).get("preferred_username", "")
    _save(cache, account=account, status="connected", last_error="", connected_at=timezone.now())
    _tok.update(v=result["access_token"], exp=time.time() + int(result.get("expires_in", 3000)))
    _flow.update(state="done", error="")
    try:                                                  # create the control tabs, list admins, sync resources
        from . import services
        services.after_connect()
    except Exception as e:  # noqa: BLE001
        log.exception("after_connect failed")
        _flow.update(error=f"Connected, but setting up the Control workbook failed: {e}"[:300])


def disconnect():
    MsToken.objects.filter(pk=1).delete()
    _tok.update(v=None, exp=0.0)
    _flow.update(state="idle", error="")


def status():
    mode = "app" if settings.GRAPH_CLIENT_SECRET else "delegated"
    row = MsToken.objects.filter(pk=1).first()
    state = "none" if not row else row.status
    if _flow["state"] == "waiting" and _flow["expires_at"] > time.time():
        state = "signing_in"
    return {"mode": mode, "state": "connected" if mode == "app" else state,
            "account": row.account if row else "", "connected_at": row.connected_at if row else None,
            "error": (_flow["error"] if _flow["state"] == "failed" else (row.last_error if row else "")),
            "flow": _flow["state"]}
