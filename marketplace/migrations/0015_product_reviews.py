from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('marketplace', '0014_product_archived_at')]
    operations = [
        migrations.AddField(model_name='product', name='rating_avg', field=models.DecimalField(max_digits=3, decimal_places=2, default=0, help_text='Average of visible customer ratings (maintained automatically).')),
        migrations.AddField(model_name='product', name='rating_count', field=models.PositiveIntegerField(default=0, help_text='Number of visible customer ratings (maintained automatically).')),
        migrations.CreateModel(
            name='ProductReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rating', models.PositiveSmallIntegerField(help_text='1 (poor) to 5 (excellent)')),
                ('customer_name', models.CharField(max_length=150)),
                ('comment', models.TextField(blank=True, max_length=1000)),
                ('is_visible', models.BooleanField(default=True, help_text='Untick to hide an inappropriate review; ratings are recalculated automatically.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews', to='marketplace.order')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews', to='marketplace.product')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(model_name='productreview', constraint=models.UniqueConstraint(fields=('product', 'order'), name='mp_one_review_per_order_product')),
        migrations.AddConstraint(model_name='productreview', constraint=models.CheckConstraint(condition=models.Q(('rating__gte', 1), ('rating__lte', 5)), name='mp_review_rating_1_to_5')),
        migrations.AddIndex(model_name='productreview', index=models.Index(fields=['product', 'is_visible', '-created_at'], name='mp_review_product_idx')),
    ]
