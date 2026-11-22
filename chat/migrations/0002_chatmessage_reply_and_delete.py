from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('chat', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='chatmessage',
            name='reply_to',
            field=models.ForeignKey(
                blank=True, help_text='The earlier message this one replies to.', null=True,
                on_delete=django.db.models.deletion.SET_NULL, related_name='replies', to='chat.chatmessage',
            ),
        ),
        migrations.AddField(
            model_name='chatmessage',
            name='deleted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
