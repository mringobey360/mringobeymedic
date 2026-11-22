from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from hq.models import PaymentSettings, SignupRequest, Subscription
from hq.packages_views import SERVICES, TIER_INFO, TIER_ORDER


class Command(BaseCommand):
    help = 'Validate configured SmartPOS packages, tiers and premium entitlements without changing data.'

    def handle(self, *args, **options):
        errors = []
        warnings = []
        settings = PaymentSettings.load()

        expected_tiers = set(TIER_ORDER)
        model_tiers = {choice[0] for choice in SignupRequest.Tier.choices}
        if expected_tiers != model_tiers:
            errors.append(f'Tier catalog mismatch: UI={sorted(expected_tiers)} model={sorted(model_tiers)}')

        if set(TIER_INFO) != expected_tiers:
            errors.append('TIER_INFO does not cover every subscription tier.')

        expected_limits = {
            'STARTER': (1, 3),
            'BUSINESS': (2, 10),
            'PROFESSIONAL': (5, 25),
            'ENTERPRISE': (999, 1000),
        }
        for tier, (branches, users) in expected_limits.items():
            # Instantiate-free comparison against the canonical Subscription property.
            limits = Subscription(owner_id=None, tier=tier).tier_limits
            if (limits['branches'], limits['users']) != (branches, users):
                errors.append(f'{tier} limits mismatch: {limits}')

        for key, service in SERVICES.items():
            package_model = service['package']
            active = package_model.objects.filter(is_active=True)
            if not active.exists():
                warnings.append(f'{key}: no active packages configured.')
            for pkg in active:
                credits = getattr(pkg, service['credits_field'], None)
                if not credits or credits <= 0:
                    errors.append(f'{key}/{pkg.code}: credits must be greater than zero.')
                if pkg.price is None or pkg.price < 0:
                    errors.append(f'{key}/{pkg.code}: invalid package price.')
                if not pkg.duration_days or pkg.duration_days <= 0:
                    errors.append(f'{key}/{pkg.code}: duration_days must be greater than zero.')

        for field in ('starter_monthly_price', 'business_monthly_price', 'professional_monthly_price', 'enterprise_monthly_price',
                      'starter_yearly_price', 'business_yearly_price', 'professional_yearly_price', 'enterprise_yearly_price'):
            if getattr(settings, field, None) is None:
                warnings.append(f'PaymentSettings.{field} is not configured.')

        if errors:
            for item in errors:
                self.stderr.write(self.style.ERROR(item))
            raise CommandError(f'Package validation failed with {len(errors)} error(s).')

        self.stdout.write(self.style.SUCCESS(
            f'Package validation passed: {len(SERVICES)} premium services, {len(TIER_ORDER)} core tiers checked.'
        ))
        for item in warnings:
            self.stdout.write(self.style.WARNING(item))
