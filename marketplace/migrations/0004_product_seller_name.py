from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('marketplace', '0003_payment_attempt')]
    operations = [migrations.AddField(
        model_name='product',
        name='seller_name',
        field=models.CharField(blank=True, db_index=True, default='', help_text='Public shop/business name. Products with the same shop name are grouped together on the product page.', max_length=150),
    )]
