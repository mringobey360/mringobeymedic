from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0002_sale_sale_number'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='debt',
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='sale',
            index=models.Index(fields=['pharmacy', '-created_at'], name='sale_pharmacy_created_idx'),
        ),
        migrations.AddIndex(
            model_name='sale',
            index=models.Index(fields=['served_by', '-created_at'], name='sale_served_by_created_idx'),
        ),
        migrations.AddIndex(
            model_name='debt',
            index=models.Index(fields=['pharmacy', 'is_settled', '-created_at'], name='debt_pharmacy_settled_idx'),
        ),
        migrations.AddIndex(
            model_name='debt',
            index=models.Index(
                Lower('customer_name'),
                name='debt_customer_name_lower_idx',
            ),
        ),
    ]
