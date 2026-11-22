from django.apps import AppConfig


class ExpensesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'expenses'
    verbose_name = 'Expenses & Cash Control'

    def ready(self):
        from . import meal_cash  # noqa: F401  (registers the MealLedger post_save sync)
