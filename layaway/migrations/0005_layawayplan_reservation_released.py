from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('layaway', '0004_layaway_refund')]

    operations = [
        migrations.AddField(
            model_name='layawayplan',
            name='reservation_released',
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
