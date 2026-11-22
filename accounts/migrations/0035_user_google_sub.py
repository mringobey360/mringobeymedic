from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0034_role_label_shop_owner')]

    operations = [
        migrations.AddField(
            model_name='user', name='google_sub',
            field=models.CharField(blank=True, db_index=True, help_text="Google account id (the 'sub' claim) linked for 'Continue with Google' sign-in. Shop Owners only.", max_length=64, null=True, unique=True),
        ),
    ]
