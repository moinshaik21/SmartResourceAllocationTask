from django.core.management.base import BaseCommand
from core.models import User


class Command(BaseCommand):
    help = "Create a demo admin and 4 demo resources (local testing only)."

    def handle(self, *a, **kw):
        User.objects.filter(email="admin@demo.com").exists() or User.objects.create_superuser(
            "admin@demo.com", "admin123", name="Admin")
        for n in ["Asha", "Ravi", "Meena", "Kiran"]:
            email = f"{n.lower()}@demo.com"
            if not User.objects.filter(email=email).exists():
                User.objects.create_user(email, "pass1234", name=n, role="resource")
        self.stdout.write("admin@demo.com / admin123   |   asha|ravi|meena|kiran@demo.com / pass1234")
