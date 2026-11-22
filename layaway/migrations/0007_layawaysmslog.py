import django.db.models.deletion
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('layaway', '0006_partial_handover')]
    operations = [
        migrations.CreateModel(
            name='LayawaySMSLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_key', models.CharField(max_length=100, unique=True)),
                ('event_type', models.CharField(choices=[('CREATED','Plan created'),('PAYMENT','Payment received'),('PAID','Fully paid'),('HANDOVER_PARTIAL','Partial handover'),('HANDOVER_COMPLETE','Handover complete'),('REMINDER_3_DAYS','Due in three days'),('REMINDER_DUE','Due today'),('REMINDER_OVERDUE','Overdue reminder')], max_length=24)),
                ('phone', models.CharField(max_length=24)),
                ('message', models.TextField()),
                ('status', models.CharField(choices=[('PENDING','Pending'),('SENT','Accepted by provider'),('FAILED','Failed'),('NO_CREDITS','Insufficient SMS credits')], db_index=True, default='PENDING', max_length=16)),
                ('provider_result', models.CharField(blank=True, max_length=255)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='layaway_sms_logs', to='pharmacies.pharmacy')),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sms_logs', to='layaway.layawayplan')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(model_name='layawaysmslog', index=models.Index(fields=['pharmacy','status','-created_at'], name='layaway_lay_pharma_8afbb1_idx')),
    ]
