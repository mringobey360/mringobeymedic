from django.core.validators import RegexValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pharmacies', '0011_receiptsettings_text_color'),
    ]

    operations = [
        migrations.AddField(
            model_name='receiptsettings',
            name='background_color',
            field=models.CharField(default='#FFFFFF', help_text='Paper colour of the receipt (light colours only).', max_length=7, validators=[RegexValidator('^#[0-9A-Fa-f]{6}$', 'Enter a hex colour such as #FFFFFF.')]),
        ),
        migrations.AddField(
            model_name='receiptsettings',
            name='panel_style',
            field=models.CharField(choices=[('TINT', 'Soft brand tint'), ('GREY', 'Light grey'), ('OUTLINE', 'Outline only')], default='TINT', help_text='Look of the details box and the total box.', max_length=7),
        ),
        migrations.AddField(
            model_name='receiptsettings',
            name='title_style',
            field=models.CharField(choices=[('LINES', 'Lines above & below'), ('BAND', 'Solid colour bar')], default='LINES', help_text='Look of the receipt title.', max_length=5),
        ),
    ]
