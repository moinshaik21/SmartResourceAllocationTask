import csv
from django.core.management.base import BaseCommand
from core import services
from core.models import User


class Command(BaseCommand):
    help = "Bulk-create resources from a CSV with columns: name,email,password"

    def add_arguments(self, parser):
        parser.add_argument("csv_file")

    def handle(self, *a, csv_file, **kw):
        with open(csv_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                email = row["email"].strip().lower()
                u, created = User.objects.get_or_create(
                    email=email, defaults={"name": row["name"].strip(), "role": "resource"})
                u.set_password(row["password"])
                u.save()
                services.sheet_upsert_user(u)
                self.stdout.write(f"{'created' if created else 'updated'}: {email}")
