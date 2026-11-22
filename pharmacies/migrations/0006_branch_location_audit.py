from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('pharmacies', '0005_attendance_geofence'), ('accounts', '0005_repair_missing_indexes')]
    operations = [
        migrations.AddField(model_name='pharmacy', name='location_accuracy_m', field=models.DecimalField(blank=True, decimal_places=2, help_text='Accuracy reported when the branch GPS point was captured.', max_digits=8, null=True)),
        migrations.AddField(model_name='pharmacy', name='location_captured_at', field=models.DateTimeField(blank=True, help_text='When the branch GPS point was last verified/captured.', null=True)),
        migrations.AddField(model_name='pharmacy', name='location_captured_by', field=models.ForeignKey(blank=True, help_text='Manager who last captured the branch GPS point.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='captured_branch_locations', to='accounts.user')),
        migrations.AddField(model_name='pharmacy', name='location_source', field=models.CharField(choices=[('DEVICE','Device GPS'),('MANUAL','Manual')], default='MANUAL', max_length=12)),
    ]
