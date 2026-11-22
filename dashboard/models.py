from django.conf import settings
from django.db import models

from . import perf_core as pc


class DashboardPreference(models.Model):
    """How a manager wants the Sales / Profit cards (and the Performance
    Monitor) to look. No row = the default: current month, daily breakdown."""

    class Mode(models.TextChoices):
        MONTH = pc.MODE_MONTH, 'This month (default)'
        LAST_DAYS = pc.MODE_LAST_DAYS, 'Last N days'
        LAST_WEEKS = pc.MODE_LAST_WEEKS, 'Last N weeks'
        LAST_MONTHS = pc.MODE_LAST_MONTHS, 'Last N months'
        CUSTOM = pc.MODE_CUSTOM, 'Custom date range'

    class Interval(models.TextChoices):
        DAY = pc.INTERVAL_DAY, 'Daily'
        WEEK = pc.INTERVAL_WEEK, 'Weekly'
        MONTH = pc.INTERVAL_MONTH, 'Monthly'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='dashboard_preference')
    mode = models.CharField(max_length=12, choices=Mode.choices, default=Mode.MONTH)
    last_n = models.PositiveSmallIntegerField(default=14, help_text='Number of days / weeks / months to look back.')
    custom_start = models.DateField(null=True, blank=True)
    custom_end = models.DateField(null=True, blank=True)
    interval = models.CharField(max_length=6, choices=Interval.choices, default=Interval.DAY)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Dashboard preference'
        # Dedicated table name: never collides with a table of the same default name
        # that may already exist in a production database with a different layout.
        db_table = 'dashboard_performance_preference'

    def __str__(self):
        return f'{self.user} - {self.get_mode_display()}'

    @property
    def is_default(self):
        return self.mode == self.Mode.MONTH
