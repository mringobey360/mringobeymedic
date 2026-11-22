from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DashboardPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(choices=[('MONTH', 'This month (default)'), ('LAST_DAYS', 'Last N days'), ('LAST_WEEKS', 'Last N weeks'), ('LAST_MONTHS', 'Last N months'), ('CUSTOM', 'Custom date range')], default='MONTH', max_length=12)),
                ('last_n', models.PositiveSmallIntegerField(default=14, help_text='Number of days / weeks / months to look back.')),
                ('custom_start', models.DateField(blank=True, null=True)),
                ('custom_end', models.DateField(blank=True, null=True)),
                ('interval', models.CharField(choices=[('DAY', 'Daily'), ('WEEK', 'Weekly'), ('MONTH', 'Monthly')], default='DAY', max_length=6)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='dashboard_preference', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'Dashboard preference', 'db_table': 'dashboard_performance_preference'},
        ),
    ]
