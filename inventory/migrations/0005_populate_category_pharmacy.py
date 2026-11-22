from django.db import migrations


def populate_category_pharmacy(apps, schema_editor):
    Category = apps.get_model('inventory', 'Category')
    Product = apps.get_model('inventory', 'Product')

    # Legacy categories were global. Assign each legacy category to the first
    # pharmacy that uses it, then clone it for every other pharmacy and move
    # that pharmacy's products to the clone.
    #
    # Products are already tenant-scoped, so this preserves the existing
    # catalog while making Category tenant-scoped as well.
    for category in list(Category.objects.all().order_by('id')):
        pharmacy_ids = list(
            Product.objects.filter(category_id=category.id)
            .exclude(pharmacy_id__isnull=True)
            .values_list('pharmacy_id', flat=True)
            .distinct()
            .order_by('pharmacy_id')
        )

        if not pharmacy_ids:
            # A legacy category that is not referenced by a product cannot be
            # safely assigned to a tenant. Remove it rather than inventing an
            # owner. This is consistent with the old unused-category cleanup.
            category.delete()
            continue

        first_pharmacy_id = pharmacy_ids[0]
        category.pharmacy_id = first_pharmacy_id
        category.save(update_fields=['pharmacy'])

        for pharmacy_id in pharmacy_ids[1:]:
            clone = Category.objects.create(
                pharmacy_id=pharmacy_id,
                name=category.name,
            )
            Product.objects.filter(
                category_id=category.id,
                pharmacy_id=pharmacy_id,
            ).update(category_id=clone.id)


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0004_category_pharmacy_tenant_scope'),
    ]

    operations = [
        migrations.RunPython(populate_category_pharmacy, migrations.RunPython.noop),
    ]
