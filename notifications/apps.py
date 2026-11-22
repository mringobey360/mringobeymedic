from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notifications'
    verbose_name = 'Notifications'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in
        from .views import ANNOUNCEMENT_POPUP_SESSION_KEY

        def _reset_announcement_popup(sender, request, user, **kwargs):
            # A new sign-in is a new chance to see unread announcements.
            try:
                request.session.pop(ANNOUNCEMENT_POPUP_SESSION_KEY, None)
            except Exception:  # pragma: no cover - never block a login
                pass

        user_logged_in.connect(_reset_announcement_popup, dispatch_uid='announcement_popup_reset')
