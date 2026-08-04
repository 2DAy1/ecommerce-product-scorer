import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or synchronize the demo user from DEMO_* environment variables."

    def handle(self, *args: object, **options: object) -> None:
        username = os.getenv("DEMO_USERNAME", "demo").strip()
        email = os.getenv("DEMO_EMAIL", "demo@example.com").strip()
        password = os.getenv("DEMO_PASSWORD", "demo12345")

        if not username:
            raise CommandError("DEMO_USERNAME must not be empty.")
        if not password:
            raise CommandError("DEMO_PASSWORD must not be empty.")

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(username=username)

        user.email = email
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save(
            update_fields=[
                "email",
                "is_active",
                "is_staff",
                "is_superuser",
                "password",
            ]
        )

        action = "created" if created else "synchronized"
        self.stdout.write(self.style.SUCCESS(f'Demo user "{username}" {action}.'))
