from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pharmacies', '0003_pharmacy_last_sale_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='branchaddon',
            name='payment_reference',
            field=models.CharField(blank=True, help_text='Mobile-money transaction reference supplied by the owner before admin verification.', max_length=120),
        ),
    ]
