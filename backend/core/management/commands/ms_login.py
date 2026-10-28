import time

import msal
from django.core.management.base import BaseCommand

from core import ms_auth


class Command(BaseCommand):
    help = "Sign in to Microsoft once from the terminal (device code) and store the token in the database."

    def handle(self, *a, **kw):
        cache = msal.SerializableTokenCache()
        app = ms_auth._app(cache)
        flow = app.initiate_device_flow(scopes=ms_auth.SCOPES)
        if "user_code" not in flow:
            raise SystemExit(flow.get("error_description") or "Could not start the sign-in")
        self.stdout.write(flow["message"])
        result = app.acquire_token_by_device_flow(flow)
        ms_auth.complete(cache, result)
        st = ms_auth.status()
        self.stdout.write(f"Connected as {st['account']}. The token is stored (encrypted) in the database.")
