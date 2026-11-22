from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("customer_registry", "0003_ensure_customer_registry_schema"),
    ]

    operations = [
        # The production database may already contain these columns from
        # an earlier deployment.  Keep the migration state authoritative
        # while making the physical schema operation idempotent.
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'customer_registry_customercampaignrecipient'
                              AND column_name = 'attempt_count'
                        ) THEN
                            ALTER TABLE customer_registry_customercampaignrecipient
                                ADD COLUMN attempt_count integer NOT NULL DEFAULT 0;
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'customer_registry_customercampaignrecipient'
                              AND column_name = 'last_attempt_at'
                        ) THEN
                            ALTER TABLE customer_registry_customercampaignrecipient
                                ADD COLUMN last_attempt_at timestamp with time zone NULL;
                        END IF;
                    END $$;
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="customercampaignrecipient",
                    name="status",
                    field=models.CharField(
                        max_length=10,
                        choices=[
                            ("PENDING", "Pending"),
                            ("SENDING", "Sending"),
                            ("SENT", "Sent"),
                            ("FAILED", "Failed"),
                        ],
                        default="PENDING",
                    ),
                ),
                migrations.AddField(
                    model_name="customercampaignrecipient",
                    name="attempt_count",
                    field=models.PositiveIntegerField(default=0),
                ),
                migrations.AddField(
                    model_name="customercampaignrecipient",
                    name="last_attempt_at",
                    field=models.DateTimeField(blank=True, null=True),
                ),
            ],
        ),
    ]
