from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('daily_reports', '0003_report_salary_accrual'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name='dailyreportpackage', name='custom_owner', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='custom_daily_report_packages', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='dailyreportpackage', name='is_custom', field=models.BooleanField(db_index=True, default=False)),
    ]
