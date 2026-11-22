from django.core.validators import RegexValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pharmacies', '0010_receiptsettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='receiptsettings',
            name='text_color',
            field=models.CharField(default='#172033', help_text='Colour of the normal receipt text (items, details, amounts).', max_length=7, validators=[RegexValidator('^#[0-9A-Fa-f]{6}$', 'Enter a hex colour such as #172033.')]),
        ),
    ]
