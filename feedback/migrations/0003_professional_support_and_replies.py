import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies = [('feedback', '0002_feedback_app_version_v50_50'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AlterField(model_name='feedback', name='app_version', field=models.CharField(default='V52', max_length=32)),
        migrations.CreateModel(name='FeedbackReply', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('message', models.TextField(max_length=3000)), ('is_internal', models.BooleanField(default=False, help_text='Internal staff note; not visible to customer.')),
            ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ('author', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='smartpos_feedback_replies', to=settings.AUTH_USER_MODEL)),
            ('feedback', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='replies', to='feedback.feedback')),
        ], options={'ordering':['created_at']}),
        migrations.CreateModel(name='SupportTicket', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('ticket_number', models.CharField(blank=True, db_index=True, max_length=20, unique=True)),
            ('subject', models.CharField(max_length=160)), ('category', models.CharField(choices=[('TECHNICAL','Technical problem'),('ACCOUNT','Account or login'),('SALES','Sales / POS'),('INVENTORY','Inventory / stock'),('BILLING','Billing or subscription'),('OTHER','Other')], default='TECHNICAL', max_length=16)),
            ('description', models.TextField(max_length=5000)), ('status', models.CharField(choices=[('OPEN','Open'),('IN_PROGRESS','In progress'),('WAITING_CUSTOMER','Waiting for customer'),('RESOLVED','Resolved'),('CLOSED','Closed')], db_index=True, default='OPEN', max_length=24)),
            ('priority', models.CharField(choices=[('LOW','Low'),('NORMAL','Normal'),('HIGH','High'),('URGENT','Urgent')], db_index=True, default='NORMAL', max_length=10)),
            ('page_url', models.CharField(blank=True, max_length=500)), ('app_version', models.CharField(default='V52', max_length=32)),
            ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)), ('updated_at', models.DateTimeField(auto_now=True)), ('resolved_at', models.DateTimeField(blank=True, null=True)),
            ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_smartpos_tickets', to=settings.AUTH_USER_MODEL)),
            ('branch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='smartpos_support_tickets', to='pharmacies.pharmacy')),
            ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='smartpos_support_tickets', to=settings.AUTH_USER_MODEL)),
        ], options={'ordering':['-updated_at','-created_at']}),
        migrations.CreateModel(name='SupportMessage', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('body', models.TextField(max_length=5000)), ('is_internal', models.BooleanField(default=False, help_text='Staff-only note, never shown to customer.')),
            ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ('author', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='smartpos_support_messages', to=settings.AUTH_USER_MODEL)),
            ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='feedback.supportticket')),
        ], options={'ordering':['created_at']}),
    ]
