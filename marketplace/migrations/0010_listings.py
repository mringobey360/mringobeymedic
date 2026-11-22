from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0009_backfill_shop_categories_and_orders'),
    ]

    operations = [
        migrations.CreateModel(
            name='Listing',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=160)),
                ('slug', models.SlugField(max_length=200, unique=True)),
                ('purpose', models.CharField(choices=[('RENT', 'For rent'), ('SALE', 'For sale')], db_index=True, default='RENT', max_length=4)),
                ('property_type', models.CharField(choices=[('HOUSE', 'House'), ('APARTMENT', 'Apartment / Flat'), ('ROOM', 'Room / Self-contained'), ('HOSTEL', 'Hostel / Guest room'), ('FRAME', 'Shop / Frame'), ('OFFICE', 'Office space'), ('WAREHOUSE', 'Warehouse / Godown'), ('LAND', 'Plot / Land'), ('FARM', 'Farm / Shamba'), ('OTHER', 'Other')], db_index=True, default='HOUSE', max_length=12)),
                ('description', models.TextField(blank=True)),
                ('price', models.DecimalField(decimal_places=2, max_digits=14)),
                ('price_period', models.CharField(choices=[('ONCE', 'one-off price'), ('MONTH', 'per month'), ('YEAR', 'per year'), ('DAY', 'per day'), ('SQM', 'per sqm')], default='MONTH', max_length=6)),
                ('negotiable', models.BooleanField(default=False)),
                ('installments', models.BooleanField(default=False, help_text='Payment in instalments is possible.')),
                ('payment_terms', models.CharField(blank=True, help_text='e.g. "3 months rent in advance" or "30% deposit, balance in 6 months".', max_length=200)),
                ('region', models.CharField(db_index=True, max_length=80)),
                ('district', models.CharField(blank=True, max_length=80)),
                ('ward', models.CharField(blank=True, max_length=80)),
                ('street', models.CharField(blank=True, max_length=120)),
                ('landmark', models.CharField(blank=True, help_text='A nearby landmark that helps people find it.', max_length=160)),
                ('latitude', models.DecimalField(blank=True, decimal_places=7, max_digits=10, null=True)),
                ('longitude', models.DecimalField(blank=True, decimal_places=7, max_digits=10, null=True)),
                ('size_value', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('size_unit', models.CharField(choices=[('SQM', 'sqm'), ('ACRE', 'acres'), ('HECTARE', 'hectares')], default='SQM', max_length=8)),
                ('bedrooms', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('bathrooms', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('title_type', models.CharField(blank=True, choices=[('', 'Not specified'), ('TITLE_DEED', 'Title deed (Hati miliki)'), ('RESIDENTIAL', 'Residential licence (Leseni ya makazi)'), ('CUSTOMARY', 'Customary right (Hati ya kimila)'), ('SALE_AGREEMENT', 'Sale agreement only'), ('OTHER', 'Other')], default='', max_length=14)),
                ('surveyed', models.BooleanField(default=False, help_text='The plot has been surveyed.')),
                ('amenities', models.JSONField(blank=True, default=list)),
                ('contact_phone', models.CharField(blank=True, max_length=25)),
                ('whatsapp_phone', models.CharField(blank=True, max_length=25)),
                ('status', models.CharField(choices=[('DRAFT', 'Draft'), ('PENDING', 'Awaiting review'), ('PUBLISHED', 'Published'), ('RESERVED', 'Reserved'), ('CLOSED', 'Sold / Rented'), ('REJECTED', 'Needs changes'), ('ARCHIVED', 'Archived')], db_index=True, default='DRAFT', max_length=10)),
                ('review_note', models.CharField(blank=True, max_length=300)),
                ('featured_until', models.DateTimeField(blank=True, null=True)),
                ('views_count', models.PositiveIntegerField(default=0)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='listings', to='marketplace.sellerstore')),
            ],
            options={'ordering': ['-published_at', '-created_at']},
        ),
        migrations.CreateModel(
            name='ListingImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='marketplace/listings/')),
                ('caption', models.CharField(blank=True, max_length=120)),
                ('position', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='images', to='marketplace.listing')),
            ],
            options={'ordering': ['position', 'pk']},
        ),
        migrations.CreateModel(
            name='Inquiry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('phone', models.CharField(max_length=25)),
                ('message', models.TextField(blank=True, max_length=1000)),
                ('visit_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(choices=[('new', 'New'), ('contacted', 'Contacted'), ('viewing', 'Viewing booked'), ('closed', 'Closed')], db_index=True, default='new', max_length=10)),
                ('seller_note', models.CharField(blank=True, max_length=300)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='inquiries', to='marketplace.listing')),
            ],
            options={'ordering': ['-created_at'], 'verbose_name_plural': 'Inquiries'},
        ),
        migrations.AddIndex(
            model_name='listing',
            index=models.Index(fields=['status', 'purpose', 'property_type'], name='mp_listing_browse_idx'),
        ),
        migrations.AddIndex(
            model_name='listing',
            index=models.Index(fields=['store', 'status'], name='mp_listing_store_idx'),
        ),
    ]
