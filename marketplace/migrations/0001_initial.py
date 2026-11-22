import uuid
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(name='Category', fields=[('id',models.BigAutoField(primary_key=True,serialize=False)),('name',models.CharField(max_length=100,unique=True)),('slug',models.SlugField(unique=True)),('is_active',models.BooleanField(default=True))], options={'ordering':['name'],'verbose_name_plural':'Categories'}),
        migrations.CreateModel(name='Product',fields=[('id',models.BigAutoField(primary_key=True,serialize=False)),('name',models.CharField(max_length=180)),('slug',models.SlugField(max_length=220,unique=True)),('description',models.TextField()),('price',models.DecimalField(decimal_places=2,max_digits=13)),('image',models.ImageField(blank=True,upload_to='marketplace/products/')),('stock',models.PositiveIntegerField(default=0)),('featured',models.BooleanField(default=False)),('is_active',models.BooleanField(default=True)),('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('category',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='products',to='marketplace.category'))],options={'ordering':['-featured','-created_at']}),
        migrations.CreateModel(name='Order',fields=[('id',models.BigAutoField(primary_key=True,serialize=False)),('reference',models.UUIDField(default=uuid.uuid4,editable=False,unique=True)),('customer_name',models.CharField(max_length=150)),('phone',models.CharField(max_length=25)),('location',models.CharField(max_length=250)),('notes',models.TextField(blank=True)),('status',models.CharField(choices=[('new','New'),('contacted','Contacted'),('confirmed','Confirmed'),('fulfilled','Fulfilled'),('cancelled','Cancelled')],default='new',max_length=20)),('total',models.DecimalField(decimal_places=2,default=0,max_digits=14)),('created_at',models.DateTimeField(auto_now_add=True))],options={'ordering':['-created_at']}),
        migrations.CreateModel(name='OrderItem',fields=[('id',models.BigAutoField(primary_key=True,serialize=False)),('name',models.CharField(max_length=180)),('quantity',models.PositiveIntegerField()),('unit_price',models.DecimalField(decimal_places=2,max_digits=13)),('order',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='items',to='marketplace.order')),('product',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='marketplace.product'))]),
    ]
