from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("hq", "0015_business_types_online_seller_real_estate"),
        ("pharmacies", "0015_pharmacy_attendance_qr_requires_location"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pharmacy",
            name="business_type",
            field=models.CharField(
                choices=[('PHARMACY', 'Pharmacy / Drug store'), ('RETAIL', 'Retail shop / Duka'), ('WHOLESALE', 'Wholesale / Distribution'), ('SUPERMARKET', 'Supermarket / Grocery'), ('RESTAURANT', 'Restaurant / Cafe'), ('HARDWARE', 'Hardware / Building materials'), ('ELECTRONICS', 'Electronics / Phones'), ('FASHION', 'Clothing / Boutique'), ('BEAUTY', 'Salon / Beauty'), ('AGROVET', 'Agro-vet / Farm inputs'), ('ONLINE_SELLER', 'Online seller (no shop needed)'), ('REAL_ESTATE', 'Property: rentals, houses, land'), ('OTHER', 'Other business')],
                default="PHARMACY", max_length=15,
                help_text="Kind of business this branch runs. Existing branches are Pharmacy.",
            ),
        ),
    ]
