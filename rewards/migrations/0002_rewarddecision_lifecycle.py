from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('rewards','0001_initial'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name='rewarddecision', name='status', field=models.CharField(choices=[('PROPOSED','Proposed'),('APPROVED','Approved'),('REJECTED','Rejected'),('PAID','Paid')], db_index=True, default='PROPOSED', max_length=12)),
        migrations.AddField(model_name='rewarddecision', name='reviewed_note', field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='rewarddecision', name='paid_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='rewarddecision', name='paid_by', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='paid_staff_rewards', to=settings.AUTH_USER_MODEL)),
        migrations.RunPython(lambda apps, schema: apps.get_model('rewards','RewardDecision').objects.filter(approved_at__isnull=False).update(status='APPROVED'), migrations.RunPython.noop),
    ]
