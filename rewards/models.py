from django.conf import settings
from django.db import models

class RewardDecision(models.Model):
    """Auditable, manager-approved monthly staff reward; never pays automatically."""
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='staff_reward_decisions')
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reward_decisions')
    period = models.CharField(max_length=7, help_text='YYYY-MM')
    rank = models.PositiveSmallIntegerField()
    metric = models.CharField(max_length=12, choices=[('SALES','Net sales'),('PROFIT','Gross profit')], default='SALES')
    metric_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    proposed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_staff_rewards')
    approved_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=[('PROPOSED','Proposed'),('APPROVED','Approved'),('REJECTED','Rejected'),('PAID','Paid')], default='PROPOSED', db_index=True)
    reviewed_note = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='paid_staff_rewards')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-period','rank']
        constraints = [models.UniqueConstraint(fields=['pharmacy','staff','period'], name='uniq_staff_reward_period')]


class VIPPolicy(models.Model):
    """Branch-scoped manager-configurable VIP qualification and discount policy."""
    pharmacy = models.OneToOneField('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='vip_policy')
    enabled = models.BooleanField(default=True)
    qualifying_visits = models.PositiveSmallIntegerField(default=4)
    window_days = models.PositiveSmallIntegerField(default=90)
    discount_type = models.CharField(max_length=10, choices=[('PERCENT', 'Percentage'), ('AMOUNT', 'Fixed amount per unit')], default='PERCENT')
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=5, help_text='Used when discount_type is PERCENT; retained for backwards compatibility.')
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='TZS discount per unit when discount_type is AMOUNT.')
    minimum_spend = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text='0 disables the minimum-spend requirement.')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='updated_vip_policies')

    class Meta:
        verbose_name = 'VIP policy'
        verbose_name_plural = 'VIP policies'

    def __str__(self):
        return f'VIP policy · {self.pharmacy}'
