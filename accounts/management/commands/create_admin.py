import getpass

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError as DjangoValidationError

from accounts.models import User
from accounts.phone_utils import normalize_phone_number
from pharmacies.models import Pharmacy


class Command(BaseCommand):
    help = "Creates the first Shop Owner / Supervisor (admin role) account and their first pharmacy branch, interactively."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Create Shop Owner / Supervisor Account"))

        username = input("Username: ").strip()
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"A user with username '{username}' already exists.")

        first_name = input("First name: ").strip()
        last_name = input("Last name: ").strip()
        phone_number = input("Phone number (e.g. 0712345678 or +255712345678): ").strip()
        try:
            phone_number = normalize_phone_number(phone_number)
        except DjangoValidationError as exc:
            raise CommandError(exc.messages[0])
        if User.objects.filter(phone_number=phone_number).exists():
            raise CommandError(f"A user with phone number '{phone_number}' already exists.")

        password = getpass.getpass("Password: ")
        password_confirm = getpass.getpass("Confirm password: ")
        if password != password_confirm:
            raise CommandError("Passwords do not match.")

        self.stdout.write(self.style.MIGRATE_HEADING("\nCreate Your First Pharmacy Branch"))
        pharmacy_name = input("Pharmacy branch name (e.g. 'Kariakoo Branch'): ").strip() or "Main Branch"
        pharmacy_location = input("Location (optional, e.g. 'Kariakoo, Dar es Salaam'): ").strip()

        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number,
            role=User.Role.ADMIN,
            is_staff=False,
            is_superuser=False,
        )

        pharmacy, created = Pharmacy.objects.get_or_create(
            name=pharmacy_name, defaults={'location': pharmacy_location}
        )
        user.managed_pharmacies.add(pharmacy)

        self.stdout.write(self.style.SUCCESS(
            f"Shop owner account '{user.username}' created successfully, managing '{pharmacy.name}'. "
            f"Log in at /accounts/login/ — Django Admin (/admin/) is reserved for a System Administrator "
            f"account, created separately with: python manage.py createsuperuser"
        ))
