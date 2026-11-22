from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hq", "0009_impersonation_session")]

    operations = [
        migrations.AddField(
            model_name="paymentsettings",
            name="enable_lipa_namba",
            field=models.BooleanField(
                default=True,
                help_text="When enabled, customers see the manual Lipa Namba option alongside online/mobile-money checkout. When disabled, the manual option is hidden from the public registration payment page.",
                verbose_name="Enable manual Lipa Namba payment",
            ),
        ),
    ]
