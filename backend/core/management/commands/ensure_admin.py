import os
from django.core.management.base import BaseCommand
from core import services
from core.models import User


class Command(BaseCommand):
    help = "Create (or update) the admin from ADMIN_EMAIL / ADMIN_PASSWORD env vars."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=os.environ.get("ADMIN_EMAIL"))
        parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD"))
        parser.add_argument("--name", default=os.environ.get("ADMIN_NAME", "Admin"))

    def handle(self, *a, email=None, password=None, name="Admin", **kw):
        if not email or not password:
            self.stdout.write("ADMIN_EMAIL / ADMIN_PASSWORD not set – skipping.")
            return
        u, created = User.objects.get_or_create(email=email.lower(), defaults={"name": name})
        u.name, u.role, u.is_staff, u.is_superuser, u.is_active = name, "admin", True, True, True
        u.set_password(password)
        u.save()
        try:
            services.sheet_upsert_user(u)   # also lists the admin in the Resources tab (when enabled)
        except Exception as e:  # noqa: BLE001 - e.g. Microsoft not connected yet: it is added right after sign-in
            self.stdout.write(f"(not added to the Resources tab yet: {e})")
        self.stdout.write(f"Admin {'created' if created else 'updated'}: {u.email}")
