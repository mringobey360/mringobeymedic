from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('rewards', '0003_vippolicy')]
    operations = [
        migrations.AddField(
            model_name='vippolicy',
            name='discount_type',
            field=models.CharField(choices=[('PERCENT', 'Percentage'), ('AMOUNT', 'Fixed amount per unit')], default='PERCENT', max_length=10),
        ),
        migrations.AddField(
            model_name='vippolicy',
            name='discount_amount',
            field=models.DecimalField(decimal_places=2, default=0, help_text='TZS discount per unit when discount_type is AMOUNT.', max_digits=12),
        ),
    ]
