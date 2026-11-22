import os
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core import serializers
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from hq.models import PaymentSettings, SignupRequest, Subscription
from pharmacies.models import Pharmacy, BranchAddOn
from sms_integration.models import SMSWallet, CustomerSMSCredentials


FIXTURE = os.path.join(
    settings.BASE_DIR, 'core', 'fixtures', 'legacy', 'database.json'
)


class Command(BaseCommand):
    help = (
        "Import the supplied real legacy database.json and convert the mringobey "
        "account to an active subscription customer without altering legacy records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--replace-existing',
            action='store_true',
            help='Flush the current database before importing the legacy fixture. Required when replacing demo data.',
        )
        parser.add_argument(
            '--yes-i-understand',
            action='store_true',
            help='Required together with --replace-existing because it permanently removes current database rows.',
        )
        parser.add_argument(
            '--skip-billing-setup',
            action='store_true',
            help='Import only the legacy fixture; do not create the mringobey subscription/signup/SMS setup.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not os.path.exists(FIXTURE):
            raise CommandError(f'Legacy fixture not found: {FIXTURE}')

        existing_users = User.objects.exists()
        if existing_users and not options['replace_existing']:
            raise CommandError(
                'The current database is not empty. This command is designed to replace demo/current rows '
                'with the supplied real legacy database. Re-run with --replace-existing '
                '--yes-i-understand after taking a database backup.'
            )

        if options['replace_existing']:
            if not options['yes_i_understand']:
                raise CommandError(
                    '--replace-existing permanently removes the current database rows before importing '
                    'the real fixture. Add --yes-i-understand to continue.'
                )
            self.stdout.write(self.style.WARNING(
                'Flushing current database rows before importing the supplied real database.json...'
            ))
            call_command('flush', interactive=False, verbosity=0)

        self.stdout.write(f'Importing exact legacy fixture: {FIXTURE}')
        call_command('loaddata', FIXTURE, verbosity=1)
        self.stdout.write(self.style.SUCCESS('Legacy database imported without editing its stored values.'))

        if not options['skip_billing_setup']:
            self._configure_mringobey()

    def _configure_mringobey(self):
        today = timezone.localdate()
        owner = User.objects.filter(username__iexact='mringobey').first()
        if owner is None:
            raise CommandError("The imported database does not contain the expected real owner account 'mringobey'.")

        if not owner.is_admin_role():
            owner.role = User.Role.ADMIN
            owner.save(update_fields=['role'])

        branches = list(
            Pharmacy.objects.filter(
                name__iexact='Mringobey medics Paranga'
            )
        ) + list(
            Pharmacy.objects.filter(
                name__iexact='Mringobey medics Kelema'
            )
        )
        # De-duplicate while retaining fixture order.
        unique = []
        seen = set()
        for branch in branches:
            if branch.pk not in seen:
                seen.add(branch.pk)
                unique.append(branch)
        branches = unique

        if len(branches) != 2:
            raise CommandError(
                "Expected the two real mringobey branches ('Mringobey medics Paranga' and "
                "'Mringobey medics Kelema') after import."
            )

        # Do not edit branch names, locations, phones, stock, sales, employees, or other legacy fields.
        for branch in branches:
            owner.managed_pharmacies.add(branch)

        # Current-month subscription: 30 days from activation; renewal starts next month.
        Subscription.objects.update_or_create(
            owner=owner,
            defaults={
                'plan': Subscription.Plan.MONTHLY,
                'status': Subscription.Status.ACTIVE,
                'start_date': today,
                'end_date': today + timedelta(days=30),
                'amount_paid': Decimal('12500'),
            },
        )

        # The first branch is the included branch. The second branch is a paid branch add-on.
        second_branch = branches[1]
        superadmin = User.objects.filter(is_superuser=True, is_active=True).order_by('id').first()
        addon, _ = BranchAddOn.objects.update_or_create(
            pharmacy=second_branch,
            defaults={
                'owner': owner,
                'monthly_price': Decimal('12500'),
                'status': BranchAddOn.Status.ACTIVE,
                'activated_at': timezone.now(),
                'next_billing_date': today + timedelta(days=30),
                'confirmed_by': superadmin,
            },
        )

        # Standard billable SMS allowance: 5,000 credits for the current paid month.
        # No price is invented here because the legacy database does not contain a
        # payment amount for this allowance.
        wallet, _ = SMSWallet.objects.update_or_create(
            owner=owner,
            defaults={'credits_granted': 5000, 'credits_used': 0},
        )
        CustomerSMSCredentials.objects.get_or_create(owner=owner)

        # The old database had no HQ signup record. Create a clearly-labelled
        # migration record so the superadmin can see the real owner's signup
        # identity/details while preserving the original legacy records unchanged.
        signup = SignupRequest.objects.filter(created_owner=owner).first()
        if signup is None:
            primary = branches[0]
            signup = SignupRequest.objects.create(
                business_name=primary.name,
                owner_first_name=owner.first_name,
                owner_last_name=owner.last_name,
                phone_number=owner.phone_number,
                email=owner.email,
                location=primary.location,
                pharmacy_council_fin=primary.pharmacy_council_fin or '',
                permit_number=primary.permit_number or '',
                permit_expiry_date=primary.permit_expiry_date,
                plan=SignupRequest.Plan.MONTHLY,
                status=SignupRequest.Status.ACTIVE,
                notes=(
                    'LEGACY MIGRATION: Imported from the supplied real database.json. '
                    'Original legacy database did not contain an HQ SignupRequest. '
                    'Subscription, second-branch add-on and 5,000 SMS allowance were marked paid/active '
                    'for the current month; next billing is one month from migration date.'
                ),
                confirmed_at=timezone.now(),
                confirmed_by=superadmin,
                created_pharmacy=primary,
                created_owner=owner,
            )
        else:
            # Link existing migration/signup record without rewriting the owner's real profile data.
            signup.status = SignupRequest.Status.ACTIVE
            signup.created_owner = owner
            signup.created_pharmacy = branches[0]
            if superadmin:
                signup.confirmed_by = superadmin
            if not signup.confirmed_at:
                signup.confirmed_at = timezone.now()
            signup.save(update_fields=['status', 'created_owner', 'created_pharmacy', 'confirmed_by', 'confirmed_at'])

        subscription = Subscription.objects.filter(owner=owner).first()
        if subscription and subscription.signup_request_id != signup.id:
            subscription.signup_request = signup
            subscription.save(update_fields=['signup_request'])

        self.stdout.write(self.style.SUCCESS(
            'mringobey production setup complete: active monthly subscription, both real branches active, '
            'second branch billed as add-on, and 5,000 standard SMS credits available for the current month.'
        ))
        self.stdout.write(
            f'Current billing period: {today:%Y-%m-%d} -> {(today + timedelta(days=30)):%Y-%m-%d}. '
            'Next billing starts after this period.'
        )
