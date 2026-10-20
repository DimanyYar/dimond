from django import forms
from django.conf import settings
from django.contrib import admin
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse

from .connection import jira_is_configured
from .models import SyncEvent, SyncRun, TrackerKind, TrackerSettings


class TrackerSettingsForm(forms.ModelForm):
    class Meta:
        model = TrackerSettings
        fields = ["tracker", "clients_see_jira_links"]

    def clean_tracker(self) -> str:
        tracker: str = self.cleaned_data["tracker"]
        if tracker == TrackerKind.JIRA and not jira_is_configured():
            raise forms.ValidationError(
                "Fill in TRACKER_JIRA_URL, TRACKER_JIRA_USERNAME and TRACKER_JIRA_PASSWORD in "
                ".env (the address with https://) and restart dimond first."
            )
        return tracker


@admin.register(TrackerSettings)
class TrackerSettingsAdmin(admin.ModelAdmin):
    form = TrackerSettingsForm
    readonly_fields = ["jira_in_env"]

    @admin.display(description="Jira from .env")
    def jira_in_env(self, obj: TrackerSettings) -> str:
        if not jira_is_configured():
            return "Not set"
        # The password stays in .env: showing it here would put it on screen and in history.
        return f"{settings.TRACKER_JIRA_URL} as {settings.TRACKER_JIRA_USERNAME}"

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, object] | None = None
    ) -> HttpResponse:
        # There is only one row, so the list would be a page with a single link.
        settings_row = TrackerSettings.load()
        return HttpResponseRedirect(
            reverse("admin:tracker_trackersettings_change", args=[settings_row.pk])
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: TrackerSettings | None = None
    ) -> bool:
        return False


class SyncEventInline(admin.TabularInline):
    model = SyncEvent
    fields = readonly_fields = ("task_key", "request", "what", "effect", "needs_attention")
    extra = 0
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: SyncRun | None = None) -> bool:
        return False


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    """The sync log until the Logs page (#16c): read-only, written by the sync alone."""

    list_display = ["started_at", "outcome", "checked", "error"]
    list_filter = ["outcome"]
    fields = readonly_fields = ("started_at", "finished_at", "outcome", "checked", "error")
    inlines = [SyncEventInline]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: SyncRun | None = None) -> bool:
        return False
