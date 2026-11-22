from django.core.validators import RegexValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    """Open registration to any business type.

    * business_type defaults to PHARMACY, so every existing registration keeps its meaning.
    * pharmacy_council_fin becomes blank-able (required by the form only for pharmacies).
    """

    dependencies = [
        ("hq", "0007_merge_0006_0006"),
    ]

    operations = [
        migrations.AddField(
            model_name="signuprequest",
            name="business_type",
            field=models.CharField(
                choices=[('PHARMACY', 'Pharmacy / Drug store'), ('RETAIL', 'Retail shop / Duka'), ('WHOLESALE', 'Wholesale / Distribution'), ('SUPERMARKET', 'Supermarket / Grocery'), ('RESTAURANT', 'Restaurant / Cafe'), ('HARDWARE', 'Hardware / Building materials'), ('ELECTRONICS', 'Electronics / Phones'), ('FASHION', 'Clothing / Boutique'), ('BEAUTY', 'Salon / Beauty'), ('AGROVET', 'Agro-vet / Farm inputs'), ('OTHER', 'Other business')],
                default="PHARMACY", max_length=15,
                help_text="Kind of business. Existing registrations are Pharmacy; new ones can be any business.",
            ),
        ),
        migrations.AlterField(
            model_name="signuprequest",
            name="business_name",
            field=models.CharField(max_length=150, help_text="Business name (pharmacy, shop, restaurant, etc.)."),
        ),
        migrations.AlterField(
            model_name="signuprequest",
            name="pharmacy_council_fin",
            field=models.CharField(
                blank=True, max_length=7,
                validators=[RegexValidator(r"^\d{7}$", "Enter the Pharmacy Council Facility Identification Number (FIN) as exactly 7 digits, e.g. 0101726.")],
                help_text="Pharmacy Council FIN — required only when the business type is Pharmacy. Official premises records use a 7-digit FIN.",
            ),
        ),
    ]
