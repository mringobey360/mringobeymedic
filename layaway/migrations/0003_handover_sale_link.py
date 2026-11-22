import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('layaway', '0002_premium_sms_packages'),
        ('sales', '0014_debtpayment_kind_note'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name='layawayplan', name='sale',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='layaway_plan', to='sales.sale'),
        ),
        migrations.AddField(
            model_name='layawayplan', name='handed_over_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='layawayplan', name='handed_over_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='layaway_handed_over', to=settings.AUTH_USER_MODEL),
        ),
    ]
