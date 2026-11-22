from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pharmacies", "0012_receiptsettings_background_style"),
        ("hq", "0008_signuprequest_business_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="pharmacy",
            name="business_type",
            field=models.CharField(
                choices=[('PHARMACY', 'Pharmacy / Drug store'), ('RETAIL', 'Retail shop / Duka'), ('WHOLESALE', 'Wholesale / Distribution'), ('SUPERMARKET', 'Supermarket / Grocery'), ('RESTAURANT', 'Restaurant / Cafe'), ('HARDWARE', 'Hardware / Building materials'), ('ELECTRONICS', 'Electronics / Phones'), ('FASHION', 'Clothing / Boutique'), ('BEAUTY', 'Salon / Beauty'), ('AGROVET', 'Agro-vet / Farm inputs'), ('OTHER', 'Other business')],
                default="PHARMACY", max_length=15,
                help_text="Kind of business this branch runs. Existing branches are Pharmacy.",
            ),
        ),
    ]
