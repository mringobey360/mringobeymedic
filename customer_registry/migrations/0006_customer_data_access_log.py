from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pharmacies', '0005_attendance_geofence'),
        ('customer_registry', '0005_marketing_segments_and_customer_tools'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerDataAccessLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('username', models.CharField(blank=True, max_length=150)),
                ('action', models.CharField(choices=[('EXPORT', 'Exported customer list'), ('EXPORT_DENIED', 'Export refused (not a manager)'), ('BULK_IMPORT', 'Bulk customer import'), ('BULK_DELETE', 'Bulk customer delete')], max_length=20)),
                ('record_count', models.PositiveIntegerField(default=0)),
                ('ip_address', models.CharField(blank=True, max_length=45)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('pharmacy', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customer_data_access_logs', to='pharmacies.pharmacy')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customer_data_access_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='customerdataaccesslog',
            index=models.Index(fields=['pharmacy', '-created_at'], name='customer_re_pharmac_7a1c0e_idx'),
        ),
        migrations.AddIndex(
            model_name='customerdataaccesslog',
            index=models.Index(fields=['action', '-created_at'], name='customer_re_action_3b9d52_idx'),
        ),
    ]
