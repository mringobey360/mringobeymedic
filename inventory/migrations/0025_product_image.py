from django.db import migrations, models
import inventory.models


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0024_productbatch_discount_cap'),
    ]

    operations = [
        migrations.AddField(
            model_name='product', name='image',
            field=models.ImageField(blank=True, null=True, upload_to=inventory.models.product_image_path,
                                    help_text='Optional product photo (JPG, PNG or WebP, up to 5 MB).'),
        ),
    ]
