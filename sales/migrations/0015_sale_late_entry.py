from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("sales", "0014_debtpayment_kind_note")]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="is_late_entry",
            field=models.BooleanField(
                db_index=True, default=False,
                help_text="Entered in the system AFTER the day it was really made (paper record).",
            ),
        ),
        migrations.AddField(
            model_name="sale",
            name="late_entry_reason",
            field=models.CharField(
                blank=True, max_length=40,
                help_text="Why it was not recorded on the day (see LateEntryReason).",
            ),
        ),
        migrations.AddField(
            model_name="sale",
            name="late_entry_note",
            field=models.CharField(
                blank=True, max_length=300,
                help_text="Free text: notebook page / paper receipt number / explanation.",
            ),
        ),
    ]
