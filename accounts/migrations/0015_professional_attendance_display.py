from django.db import migrations, models
from accounts.models import phone_format_validator


class Migration(migrations.Migration):
    dependencies = [
        ('pharmacies', '0007_professional_presence_settings'),
        ('accounts', '0014_user_attendance_methods'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('ADMIN', 'Shop Owner / Supervisor'),
                    ('ASSISTANT', 'Manager Assistant'),
                    ('EMPLOYEE', 'Shop Employee'),
                    ('BRANCH_DISPLAY', 'Branch Attendance Display'),
                ],
                default='EMPLOYEE',
            ),
        ),
        migrations.AlterField(
            model_name='user',
            name='phone_number',
            field=models.CharField(
                blank=True,
                max_length=20,
                null=True,
                unique=True,
                validators=[phone_format_validator],
                help_text='International format, e.g. +255712345678 (display accounts do not require a phone number).',
            ),
        ),
        migrations.AddField(
            model_name='attendancerecord',
            name='outside_alert_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='AttendanceCheckpoint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('checkpoint_type', models.CharField(max_length=12, choices=[('LUNCH', 'Lunch verification')])),
                ('scanned_at', models.DateTimeField(auto_now_add=True)),
                ('method', models.CharField(max_length=10, choices=[('QR', 'Dynamic branch QR'), ('GEOFENCE', 'GPS geofence'), ('MANUAL', 'Manager approved')])),
                ('latitude', models.DecimalField(blank=True, decimal_places=7, max_digits=10, null=True)),
                ('longitude', models.DecimalField(blank=True, decimal_places=7, max_digits=10, null=True)),
                ('accuracy_m', models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('device_fingerprint', models.CharField(blank=True, max_length=128)),
                ('note', models.CharField(blank=True, max_length=255)),
                ('attendance', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='checkpoints', to='accounts.attendancerecord')),
            ],
            options={
                'ordering': ['-scanned_at'],
                'indexes': [models.Index(fields=['attendance', '-scanned_at'], name='accounts_atte_checkpt_idx')],
                'constraints': [models.UniqueConstraint(fields=('attendance', 'checkpoint_type'), name='unique_attendance_checkpoint_type')],
            },
        ),
    ]
