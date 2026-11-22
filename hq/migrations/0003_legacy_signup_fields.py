from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hq', '0002_paymentsettings_branch_addon_price'),
    ]

    operations = [
        migrations.AlterField(
            model_name='signuprequest',
            name='permit_number',
            field=models.CharField(
                blank=True,
                help_text='Required for new registrations; may be blank on imported legacy accounts where the legacy database did not store it.',
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='signuprequest',
            name='permit_expiry_date',
            field=models.DateField(
                blank=True,
                help_text='Required for new registrations; may be blank on imported legacy accounts where the legacy database did not store it.',
                null=True,
            ),
        ),
    ]
