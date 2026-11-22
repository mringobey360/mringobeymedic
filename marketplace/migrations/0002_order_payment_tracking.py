from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('marketplace', '0001_initial')]
    operations = [
        migrations.AddField(model_name='order', name='payment_status', field=models.CharField(choices=[('unpaid','Unpaid'),('pending','Payment pending'),('paid','Paid'),('failed','Failed'),('refunded','Refunded')], default='unpaid', max_length=20)),
        migrations.AddField(model_name='order', name='payment_reference', field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name='order', name='payment_method', field=models.CharField(blank=True, max_length=60)),
        migrations.AddField(model_name='order', name='paid_at', field=models.DateTimeField(blank=True, null=True)),
    ]
