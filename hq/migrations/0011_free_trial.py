from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hq", "0010_paymentsettings_enable_lipa_namba")]

    operations = [
        migrations.AddField(
            model_name="signuprequest",
            name="is_trial",
            field=models.BooleanField(
                default=False,
                help_text="Free trial registration: activated immediately with Starter features for a limited number of days, no payment taken.",
            ),
        ),
        migrations.AddField(
            model_name="subscription",
            name="is_trial",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Free trial subscription (Starter features). Locks completely when it ends; cleared automatically when the customer upgrades.",
            ),
        ),
    ]
