from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


ORDER_STATUS = [
    ('new', 'New'), ('contacted', 'Contacted'), ('confirmed', 'Confirmed'),
    ('out_for_delivery', 'Out for delivery'), ('fulfilled', 'Delivered'), ('cancelled', 'Cancelled'),
]


class Migration(migrations.Migration):
    dependencies = [
        ('marketplace', '0007_online_service_request'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ---- Categories become private to one shop ----
        migrations.AddField(
            model_name='category', name='seller_store',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='categories', to='marketplace.sellerstore'),
        ),
        migrations.AlterField(model_name='category', name='name', field=models.CharField(max_length=100)),
        migrations.AlterField(model_name='category', name='slug', field=models.SlugField(max_length=120, unique=True)),
        migrations.AddConstraint(
            model_name='category',
            constraint=models.UniqueConstraint(condition=models.Q(seller_store__isnull=True), fields=('name',), name='mk_category_platform_name_uniq'),
        ),
        migrations.AddConstraint(
            model_name='category',
            constraint=models.UniqueConstraint(condition=models.Q(seller_store__isnull=False), fields=('seller_store', 'name'), name='mk_category_store_name_uniq'),
        ),

        # ---- Orders belong to one shop and follow a delivery workflow ----
        migrations.AddField(model_name='order', name='seller_store', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='marketplace.sellerstore')),
        migrations.AddField(model_name='order', name='seller_note', field=models.TextField(blank=True, help_text='Internal note for the shop; never shown to the customer.')),
        migrations.AddField(model_name='order', name='cancel_reason', field=models.CharField(blank=True, max_length=300)),
        migrations.AddField(model_name='order', name='dispatched_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='order', name='delivered_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='order', name='cancelled_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='order', name='stock_restored', field=models.BooleanField(default=False)),
        migrations.AlterField(model_name='order', name='status', field=models.CharField(choices=ORDER_STATUS, default='new', max_length=20)),
        migrations.AddIndex(model_name='order', index=models.Index(fields=['seller_store', 'status', 'created_at'], name='mp_order_store_status_idx')),
        migrations.AddField(model_name='paymentattempt', name='seller_store', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payment_attempts', to='marketplace.sellerstore')),
        migrations.CreateModel(
            name='OrderStatusEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=ORDER_STATUS, max_length=20)),
                ('note', models.CharField(blank=True, max_length=300)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='marketplace_order_events', to=settings.AUTH_USER_MODEL)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='marketplace.order')),
            ],
            options={'ordering': ['created_at', 'pk']},
        ),
    ]
