from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sms_integration', '0011_smsprospect'),
    ]

    operations = [
        migrations.AlterField(
            model_name='smsprospect',
            name='business_type',
            field=models.CharField(choices=[('RETAIL', 'Retail shop'), ('WHOLESALE', 'Wholesale shop'), ('HARDWARE', 'Hardware'), ('PHARMACY', 'Pharmacy'), ('COSMETICS', 'Cosmetics'), ('FOOD', 'Food / Restaurant'), ('OTHER', 'Other')], default='OTHER', max_length=12),
        ),
        migrations.AlterField(
            model_name='smsprospect',
            name='status',
            field=models.CharField(choices=[('NEW', 'New'), ('CONTACTED', 'Contacted'), ('JOINED', 'Joined'), ('OPTED_OUT', 'Opted out')], db_index=True, default='NEW', max_length=10),
        ),
    ]
