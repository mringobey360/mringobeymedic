from django.db import migrations, models


class Migration(migrations.Migration):
    """Per-product switch: medicines/perishables keep forcing an expiry date on
    every batch, while products marked as non-expiring (cosmetics, accessories,
    ...) can be stocked without one. Defaults to True so existing pharmacy
    products keep today's expected behaviour until an owner opts a specific
    product out."""

    dependencies = [
        ('inventory', '0013_batch_expiry_optional'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='requires_expiry',
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Bidhaa hii inaisha muda (mf. dawa). Ikiwa imewashwa, kila batch/stock "
                    "mpya ya bidhaa hii LAZIMA iwe na tarehe ya mwisho wa matumizi. Zima kwa "
                    "bidhaa zisizoisha muda kama vipodozi na vifaa."
                ),
            ),
        ),
    ]
