from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the ONLINE_SELLER and REAL_ESTATE business types (choices only; no data change)."""

    dependencies = [
        ("hq", "0014_referral_five_invites_15_percent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="signuprequest",
            name="business_type",
            field=models.CharField(
                choices=[('PHARMACY', 'Pharmacy / Drug store'), ('RETAIL', 'Retail shop / Duka'), ('WHOLESALE', 'Wholesale / Distribution'), ('SUPERMARKET', 'Supermarket / Grocery'), ('RESTAURANT', 'Restaurant / Cafe'), ('HARDWARE', 'Hardware / Building materials'), ('ELECTRONICS', 'Electronics / Phones'), ('FASHION', 'Clothing / Boutique'), ('BEAUTY', 'Salon / Beauty'), ('AGROVET', 'Agro-vet / Farm inputs'), ('ONLINE_SELLER', 'Online seller (no shop needed)'), ('REAL_ESTATE', 'Property: rentals, houses, land'), ('OTHER', 'Other business')],
                default="PHARMACY", max_length=15,
                help_text="Kind of business. Existing registrations are Pharmacy; new ones can be any business.",
            ),
        ),
    ]
