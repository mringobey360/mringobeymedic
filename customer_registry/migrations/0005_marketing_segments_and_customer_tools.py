from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("customer_registry", "0004_campaign_delivery_tracking")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'customer_registry_customercampaign'
                              AND column_name = 'total_segments'
                        ) THEN
                            ALTER TABLE customer_registry_customercampaign
                                ADD COLUMN total_segments integer NOT NULL DEFAULT 0;
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'customer_registry_customercampaignrecipient'
                              AND column_name = 'segments'
                        ) THEN
                            ALTER TABLE customer_registry_customercampaignrecipient
                                ADD COLUMN segments smallint NOT NULL DEFAULT 1;
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'customer_registry_customercampaignrecipient'
                              AND column_name = 'credits_reserved'
                        ) THEN
                            ALTER TABLE customer_registry_customercampaignrecipient
                                ADD COLUMN credits_reserved smallint NOT NULL DEFAULT 0;
                        END IF;
                    END $$;
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="customercampaign",
                    name="total_segments",
                    field=models.PositiveIntegerField(
                        default=0,
                        help_text="Total marketing SMS credits reserved/used by this campaign.",
                    ),
                ),
                migrations.AddField(
                    model_name="customercampaignrecipient",
                    name="segments",
                    field=models.PositiveSmallIntegerField(
                        default=1,
                        help_text="Billable SMS segments for this personalised message.",
                    ),
                ),
                migrations.AddField(
                    model_name="customercampaignrecipient",
                    name="credits_reserved",
                    field=models.PositiveSmallIntegerField(
                        default=0,
                        help_text="Marketing credits currently reserved for this recipient.",
                    ),
                ),
            ],
        ),
    ]
