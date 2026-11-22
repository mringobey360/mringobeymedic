from django.db import migrations, models


def backfill_sale_numbers(apps, schema_editor):
    Sale = apps.get_model('sales', 'Sale')
    Pharmacy = apps.get_model('pharmacies', 'Pharmacy')

    for pharmacy in Pharmacy.objects.all():
        sales = Sale.objects.filter(pharmacy=pharmacy).order_by('created_at', 'id')
        number = 0
        for sale in sales:
            number += 1
            sale.sale_number = number
            sale.save(update_fields=['sale_number'])
        if number:
            pharmacy.last_sale_number = number
            pharmacy.save(update_fields=['last_sale_number'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0001_initial'),
        ('pharmacies', '0003_pharmacy_last_sale_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='sale_number',
            field=models.PositiveIntegerField(
                blank=True, editable=False, null=True,
                help_text="Sequential number scoped to this branch only — branch #1's first sale is always #1, "
                          "regardless of how many sales other branches have made. Assigned automatically.",
            ),
        ),
        migrations.RunPython(backfill_sale_numbers, noop_reverse),
        migrations.AddConstraint(
            model_name='sale',
            constraint=models.UniqueConstraint(fields=('pharmacy', 'sale_number'), name='unique_sale_number_per_pharmacy'),
        ),
    ]
