from django.db import migrations


class Migration(migrations.Migration):
    # The presence-monitoring migration and the production meal-index repair
    # were both created from accounts.0004.  This migration intentionally
    # performs no schema operation; it only joins the two migration branches
    # into one linear graph for production deployments.
    dependencies = [
        ("accounts", "0005_professional_presence_monitoring"),
        ("accounts", "0006_shorten_meal_index_names"),
    ]

    operations = []
