import uuid
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('marketplace', '0002_order_payment_tracking')]
    operations = [migrations.CreateModel(name='PaymentAttempt', fields=[
        ('id', models.BigAutoField(primary_key=True, serialize=False)),
        ('reference', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
        ('customer_name', models.CharField(max_length=150)),
        ('phone', models.CharField(max_length=25)),
        ('location', models.CharField(max_length=250)),
        ('notes', models.TextField(blank=True)),
        ('items_snapshot', models.JSONField(default=list)),
        ('total', models.DecimalField(max_digits=14, decimal_places=2)),
        ('status', models.CharField(choices=[('pending','Pending'),('failed','Failed'),('paid','Paid')], default='pending', max_length=20)),
        ('payment_reference', models.CharField(blank=True, max_length=120)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
    ])]
