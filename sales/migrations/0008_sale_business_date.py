from django.db import migrations, models
import django.utils.timezone


def backfill_business_date(apps, schema_editor):
    Sale = apps.get_model('sales', 'Sale')
    # Existing records are historical; their accounting date should match the
    # local Tanzania calendar date represented by their created_at timestamp.
    from zoneinfo import ZoneInfo
    tz = ZoneInfo('Africa/Dar_es_Salaam')
    for sale in Sale.objects.only('id', 'created_at').iterator(chunk_size=1000):
        sale.business_date = sale.created_at.astimezone(tz).date()
        sale.save(update_fields=['business_date'])


class Migration(migrations.Migration):
    dependencies = [('sales', '0007_sale_returns_and_voids')]
    operations = [
        migrations.AddField(
            model_name='sale', name='business_date',
            field=models.DateField(db_index=True, default=django.utils.timezone.localdate),
        ),
        migrations.RunPython(backfill_business_date, migrations.RunPython.noop),
    ]
