from django.contrib import admin
from django.http import HttpRequest

from .models import Email


@admin.register(Email)
class EmailAdmin(admin.ModelAdmin):
    """The email journal, read-only: until Logs → Emails, this is where the PM checks it."""

    list_display = ["created_at", "to", "kind", "subject", "status", "error"]
    list_filter = ["status", "kind"]
    search_fields = ["to", "subject"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Email | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Email | None = None) -> bool:
        return False
