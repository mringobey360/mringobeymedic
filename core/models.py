from django.conf import settings
from django.db import models


class UserDataBackup(models.Model):
    """A tenant-isolated, user-friendly data export owned by one shop owner."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='data_backups',
    )
    filename = models.CharField(max_length=180)
    storage_path = models.CharField(max_length=500)
    size_bytes = models.PositiveBigIntegerField(default=0)
    record_counts = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def size_mb(self):
        return self.size_bytes / (1024 * 1024) if self.size_bytes else 0

    def __str__(self):
        return f"{self.owner.username} — {self.filename}"
