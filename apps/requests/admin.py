from django.contrib import admin
from django.http import HttpRequest

from .models import Request, StatusEvent


class StatusEventInline(admin.TabularInline):
    model = StatusEvent
    fields = ["created_at", "from_status", "to_status", "actor"]
    readonly_fields = ["created_at", "from_status", "to_status", "actor"]
    extra = 0

    def has_add_permission(self, request: HttpRequest, obj: Request | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Request | None = None) -> bool:
        return False


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    """Read-only: requests are created and moved through services, so the log stays whole."""

    list_display = ["title", "company", "type", "status", "author", "created_at"]
    list_filter = ["company", "type", "status"]
    search_fields = ["title"]
    inlines = [StatusEventInline]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Request | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Request | None = None) -> bool:
        return False
