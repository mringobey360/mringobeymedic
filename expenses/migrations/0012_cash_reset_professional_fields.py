from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('expenses', '0011_cash_control_reset')]
    operations = [
        migrations.AddField('cashcontrolreset', 'previous_opening', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
        migrations.AddField('cashcontrolreset', 'method', models.CharField(choices=[('START_OF_DAY', 'Known opening cash'), ('COUNT_NOW', 'Physical count now (opening calculated)')], default='START_OF_DAY', max_length=16)),
        migrations.AddField('cashcontrolreset', 'counted_cash', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
        migrations.AddField('cashcontrolreset', 'movement_at_reset', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
        migrations.AddField('cashcontrolreset', 'expected_after', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
        migrations.AddField('cashcontrolreset', 'expected_before', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
    ]
