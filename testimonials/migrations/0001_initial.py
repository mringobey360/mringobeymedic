import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name='TestimonialSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=False, help_text='Master switch. When off, no customer sees the rating popup (authorised invitations wait).')),
                ('snooze_days', models.PositiveSmallIntegerField(default=3, help_text='Days before the popup reappears after a customer taps "Later".', validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(60)])),
                ('show_on_landing', models.BooleanField(default=True, help_text='Show the approved-reviews carousel on the public landing page.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'Testimonial settings', 'verbose_name_plural': 'Testimonial settings'},
        ),
        migrations.CreateModel(
            name='TestimonialInvitation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('AUTHORIZED', 'Authorised (waiting for customer)'), ('ANSWERED', 'Answered'), ('DECLINED', 'Declined by customer'), ('REVOKED', 'Revoked by admin')], db_index=True, default='AUTHORIZED', max_length=12)),
                ('authorized_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('first_shown_at', models.DateTimeField(blank=True, null=True)),
                ('snoozed_until', models.DateTimeField(blank=True, null=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('authorized_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='testimonial_invitations', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-authorized_at']},
        ),
        migrations.AddConstraint(
            model_name='testimonialinvitation',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'AUTHORIZED')), fields=('customer',), name='testimonials_one_open_invite_per_customer'),
        ),
        migrations.CreateModel(
            name='TestimonialReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rating', models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)])),
                ('comment', models.CharField(max_length=600)),
                ('display_name', models.CharField(blank=True, max_length=80)),
                ('business_name', models.CharField(blank=True, max_length=80)),
                ('consent_public_name', models.BooleanField(default=False, help_text='Customer agreed to show their name and business publicly.')),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')], db_index=True, default='PENDING', max_length=10)),
                ('moderated_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='testimonial_reviews', to=settings.AUTH_USER_MODEL)),
                ('invitation', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='review', to='testimonials.testimonialinvitation')),
                ('moderated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='TestimonialAuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(db_index=True, max_length=40)),
                ('detail', models.CharField(blank=True, max_length=240)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('invitation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='testimonials.testimonialinvitation')),
                ('review', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='testimonials.testimonialreview')),
                ('target_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
