from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from pharmacies.models import Pharmacy
from hq.models import PaymentSettings, SignupRequest, Subscription


class Command(BaseCommand):
    help = (
        "Seeds demo data for the HQ superadmin dashboard (payment settings + sample "
        "signups in every status/plan) so you can test it without waiting for real customers."
    )

    def handle(self, *args, **options):
        settings_obj, created = PaymentSettings.objects.get_or_create(pk=1, defaults={
            'lipa_namba': '5678900',
            'payee_name': 'Kilo Mringo',
            'support_phone': '+255700000000',
            'owner_notification_phone': '+255700000000',
            'instructions_text': (
                "Tuma ushahidi wa malipo (screenshot) WhatsApp namba +255700000000 "
                "baada ya kulipa ili tuthibitishe haraka."
            ),
        })
        self.stdout.write(self.style.SUCCESS(
            "PaymentSettings " + ("created." if created else "already existed — left unchanged.")
        ))

        demo_signups = [
            dict(business_name="Duka la Dawa Neema", owner_first_name="Asha", owner_last_name="Mushi",
                 phone_number="+255712000001", email="asha@example.com", location="Kariakoo, Dar es Salaam",
                 pharmacy_council_fin="0101726", permit_number="02554-2026",
                 permit_expiry_date=timezone.localdate() + timedelta(days=365),
                 plan=SignupRequest.Plan.MONTHLY, status=SignupRequest.Status.PENDING_PAYMENT),
            dict(business_name="Afya Bora Pharmacy", owner_first_name="Juma", owner_last_name="Kessy",
                 phone_number="+255712000002", email="", location="Mwanza",
                 pharmacy_council_fin="0103055", permit_number="03055-2026",
                 permit_expiry_date=timezone.localdate() + timedelta(days=365),
                 plan=SignupRequest.Plan.YEARLY, status=SignupRequest.Status.PENDING_PAYMENT),
            dict(business_name="Baraka Pharmacy", owner_first_name="Rashid", owner_last_name="Omary",
                 phone_number="+255712000004", email="", location="Mbeya",
                 pharmacy_council_fin="0300453", permit_number="0453-2026",
                 permit_expiry_date=timezone.localdate() + timedelta(days=365),
                 plan=SignupRequest.Plan.MONTHLY, status=SignupRequest.Status.REJECTED,
                 notes="Hakulipia ndani ya siku 7."),
        ]

        created_count = 0
        for data in demo_signups:
            _, was_created = SignupRequest.objects.get_or_create(
                business_name=data['business_name'], defaults=data
            )
            if was_created:
                created_count += 1

        self.stdout.write(self.style.SUCCESS(f"{created_count} demo signup request(s) created."))

        # One fully-activated example so the Subscriptions page has something to show.
        active_signup, was_created = SignupRequest.objects.get_or_create(
            business_name="Uzima Pharmacy Demo",
            defaults=dict(
                owner_first_name="Fatuma", owner_last_name="Ally", phone_number="+255712000005",
                email="", location="Dodoma",
                pharmacy_council_fin="0102479", permit_number="02479-2026",
                permit_expiry_date=timezone.localdate() + timedelta(days=365),
                plan=SignupRequest.Plan.YEARLY,
                status=SignupRequest.Status.ACTIVE,
            ),
        )
        if was_created:
            pharmacy, _ = Pharmacy.objects.get_or_create(
                name="Uzima Pharmacy Demo", defaults={'location': 'Dodoma'}
            )
            owner, owner_created = User.objects.get_or_create(
                username='uzima.demo.owner',
                defaults=dict(
                    first_name='Fatuma', last_name='Ally', phone_number='+255712000005',
                    role=User.Role.ADMIN, is_staff=False, is_superuser=False,
                ),
            )
            if owner_created:
                owner.set_password('DemoPass123!')
                owner.save()
            owner.managed_pharmacies.add(pharmacy)

            active_signup.created_pharmacy = pharmacy
            active_signup.created_owner = owner
            active_signup.confirmed_at = timezone.now()
            active_signup.save()

            Subscription.objects.get_or_create(
                owner=owner, defaults=dict(
                    signup_request=active_signup, plan=Subscription.Plan.YEARLY,
                    status=Subscription.Status.ACTIVE, start_date=timezone.localdate() - timedelta(days=300),
                    end_date=timezone.localdate() + timedelta(days=65), amount_paid=Decimal('150000'),
                ),
            )
            self.stdout.write(self.style.SUCCESS(
                "Demo active subscription created (owner login: uzima.demo.owner / DemoPass123!)."
            ))

        self.stdout.write(self.style.SUCCESS(
            "\nDone. Visit /hq/ (as a superuser) to see the dashboard, "
            "or /jiunge/ to try the public signup form yourself."
        ))
