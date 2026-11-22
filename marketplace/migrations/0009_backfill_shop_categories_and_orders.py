from django.db import migrations
from django.utils.text import slugify


def _free_slug(Category, base):
    base = (slugify(base) or 'category')[:112].strip('-') or 'category'
    candidate, n = base, 2
    while Category.objects.filter(slug=candidate).exists():
        candidate = f'{base}-{n}'
        n += 1
    return candidate


def forwards(apps, schema_editor):
    Category = apps.get_model('marketplace', 'Category')
    Product = apps.get_model('marketplace', 'Product')
    Order = apps.get_model('marketplace', 'Order')
    PaymentAttempt = apps.get_model('marketplace', 'PaymentAttempt')

    # 1. A platform category that is shared by several shops is copied once per shop, so that
    #    every shop only ever sees (and sells under) its own categories.
    pairs = (Product.objects.filter(seller_store__isnull=False, category__seller_store__isnull=True)
             .values_list('seller_store_id', 'category_id').distinct())
    for store_id, category_id in list(pairs):
        source = Category.objects.get(pk=category_id)
        store_slug = apps.get_model('marketplace', 'SellerStore').objects.get(pk=store_id).slug
        mine = Category.objects.filter(seller_store_id=store_id, name=source.name).first()
        if mine is None:
            mine = Category.objects.create(
                seller_store_id=store_id, name=source.name, is_active=source.is_active,
                slug=_free_slug(Category, f'{store_slug}-{source.slug}'),
            )
        Product.objects.filter(seller_store_id=store_id, category_id=category_id).update(category_id=mine.pk)

    # 2. Link historical orders / checkout snapshots to the shop that sold the goods.
    for order in Order.objects.filter(seller_store__isnull=True).iterator():
        item = order.items.select_related('product').first()
        if item and item.product.seller_store_id:
            Order.objects.filter(pk=order.pk).update(seller_store_id=item.product.seller_store_id)
    for attempt in PaymentAttempt.objects.filter(seller_store__isnull=True).iterator():
        rows = attempt.items_snapshot or []
        try:
            first_id = int(rows[0]['product_id'])
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        product = Product.objects.filter(pk=first_id).only('seller_store_id').first()
        if product and product.seller_store_id:
            PaymentAttempt.objects.filter(pk=attempt.pk).update(seller_store_id=product.seller_store_id)


class Migration(migrations.Migration):
    dependencies = [('marketplace', '0008_shop_scoped_categories_and_order_workflow')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
