from django.core.management.base import BaseCommand

from accounts.models import User
from notifications.models import Notification
from notifications.utils import notify


class Command(BaseCommand):
    help = "Seeds a handful of demo notifications (all categories/levels) for every existing user, so you can see the bell in action."

    def handle(self, *args, **options):
        users = list(User.objects.all()[:10])
        if not users:
            self.stdout.write(self.style.WARNING("Hakuna watumiaji bado — tengeneza owner/employee kwanza."))
            return

        samples = [
            ("Tangazo kutoka kwa Boss", "Kesho tunafungua saa moja mapema kwa ajili ya ukaguzi wa stock.",
             Notification.Category.ANNOUNCEMENT, Notification.Level.INFO),
            ("Stock Imekaribia Kuisha", "Paracetamol 500mg imebaki chini ya kiwango cha chini.",
             Notification.Category.STOCK_ALERT, Notification.Level.WARNING),
            ("Dawa Zimeingizwa Kimakosa", "Batch ya Amoxicillin ina bei ya kuuzia chini ya bei ya kununua — tafadhali kagua.",
             Notification.Category.STOCK_ALERT, Notification.Level.DANGER),
            ("Deni Limelipwa", "Mteja Rehema Juma amelipa deni lake kamili la TZS 15,000.",
             Notification.Category.DEBT, Notification.Level.SUCCESS),
            ("Mauzo ya Leo", "Umefikia lengo la mauzo ya leo — hongera!",
             Notification.Category.SALES, Notification.Level.SUCCESS),
            ("Ujumbe Mpya", "Una ujumbe mpya kutoka kwa msimamizi wako.",
             Notification.Category.CHAT, Notification.Level.INFO),
            ("Karibu kwenye Mfumo", "Akaunti yako imewekwa tayari kikamilifu.",
             Notification.Category.SYSTEM, Notification.Level.INFO),
        ]

        created_count = 0
        for user in users:
            for title, message, category, level in samples:
                notify(user, title, message, category=category, level=level)
                created_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Created {created_count} demo notifications across {len(users)} user(s). "
            "Ingia na uangalie bell (juu kulia) kwenye ukurasa wowote."
        ))
