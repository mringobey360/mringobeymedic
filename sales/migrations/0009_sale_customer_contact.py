from django.db import migrations, models
import django.db.models.deletion

def link_existing_sales(apps, schema_editor):
    Sale = apps.get_model("sales", "Sale")
    CustomerContact = apps.get_model("customer_registry", "CustomerContact")
    for sale in Sale.objects.exclude(customer_phone="").iterator():
        contact = CustomerContact.objects.filter(
            pharmacy_id=sale.pharmacy_id,
            phone=sale.customer_phone,
            is_active=True,
        ).order_by("id").first()
        if contact:
            Sale.objects.filter(pk=sale.pk).update(customer_contact_id=contact.pk)

class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0008_sale_business_date"),
        ("customer_registry", "0005_marketing_segments_and_customer_tools"),
    ]

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
                              AND table_name = 'sales_sale'
                              AND column_name = 'customer_contact_id'
                        ) THEN
                            ALTER TABLE sales_sale
                                ADD COLUMN customer_contact_id bigint NULL;
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_constraint
                            WHERE conname = 'sales_sale_customer_contact_id_fk_customer_registry'
                        ) THEN
                            ALTER TABLE sales_sale
                                ADD CONSTRAINT sales_sale_customer_contact_id_fk_customer_registry
                                FOREIGN KEY (customer_contact_id)
                                REFERENCES customer_registry_customercontact(id)
                                DEFERRABLE INITIALLY DEFERRED;
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM pg_indexes
                            WHERE schemaname = 'public'
                              AND indexname = 'sales_sale_customer_contact_id_idx'
                        ) THEN
                            CREATE INDEX sales_sale_customer_contact_id_idx
                                ON sales_sale (customer_contact_id);
                        END IF;
                    END $$;
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="sale",
                    name="customer_contact",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sales",
                        to="customer_registry.customercontact",
                        help_text="Optional master customer contact. Sale name/phone remain a historical snapshot.",
                    ),
                ),
            ],
        ),
        migrations.RunPython(link_existing_sales, migrations.RunPython.noop),
    ]
