from django.db import migrations


class Migration(migrations.Migration):
    # Both 0006 migrations were created from the same 0005 parent.
    # This merge keeps BOTH branches and gives Django a single leaf node.
    dependencies = [
        ("hq", "0006_premium_daily_reports"),
        ("hq", "0006_saas_tiers_pricing"),
    ]

    operations = []
