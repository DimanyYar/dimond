from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from . import services
from .forms import fill_jira_projects
from .models import Company, User


class PeopleInline(admin.TabularInline):
    model = User
    fields = ["email", "name", "is_active"]
    readonly_fields = ["email", "name", "is_active"]
    extra = 0
    show_change_link = True

    def has_add_permission(self, request: HttpRequest, obj: Company | None = None) -> bool:
        # People are added on the Users page, where the same validation applies to everyone.
        return False


class CompanyAdminForm(forms.ModelForm):
    """The Jira project is picked from the projects the tracker sees, as in the New client
    window. If the tracker cannot be reached, the key can still be typed by hand."""

    class Meta:
        model = Company
        fields = ["name", "owner", "jira_project", "email_domain", "color"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        current = self.instance.jira_project if self.instance.pk else ""
        listed = forms.ChoiceField(required=False, label="Jira project")
        problem = fill_jira_projects(listed, keep=current)
        if problem:
            self.fields["jira_project"].help_text = f"{problem} Type the key by hand, like FI."
            return
        listed.choices = [("", "—"), *cast(list[tuple[str, str]], listed.choices)]
        listed.help_text = self.fields["jira_project"].help_text
        self.fields["jira_project"] = listed


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    form = CompanyAdminForm
    list_display = ["name", "owner", "jira_project", "email_domain", "created_at"]
    list_filter = ["owner"]
    search_fields = ["name", "email_domain", "jira_project"]
    inlines = [PeopleInline]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Company]:
        return super().get_queryset(request).select_related("owner")


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ["email", "name", "role", "company", "status", "last_login"]
    list_filter = ["role", "company", "is_active"]
    search_fields = ["email", "name"]
    fields = [
        "email",
        "name",
        "role",
        "company",
        "notify",
        "time_zone",
        "is_active",
        "is_staff",
        "date_joined",
        "last_login",
    ]
    readonly_fields = ["date_joined", "last_login"]
    actions = ["send_invite", "revoke_access"]

    def get_queryset(self, request: HttpRequest) -> QuerySet[User]:
        return super().get_queryset(request).select_related("company")

    @admin.display(description="Status")
    def status(self, user: User) -> str:
        if not user.is_active:
            return "Revoked"
        return "Invited" if user.last_login is None else "Active"

    @admin.action(description="Send invite (link valid 7 days)")
    def send_invite(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        assert isinstance(request.user, User)  # the admin is only open to signed-in staff
        queued = services.invite(queryset, invited_by=request.user)
        skipped = queryset.count() - queued
        self.message_user(request, f"Invites queued: {queued}. They arrive within a minute.")
        if skipped:
            self.message_user(
                request,
                f"Skipped {skipped} without access. Turn access on first, then invite.",
                messages.WARNING,
            )

    @admin.action(description="Revoke access")
    def revoke_access(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        revoked = services.revoke_access(queryset)
        self.message_user(request, f"Access revoked for {revoked}. Their requests stay.")
        owners = queryset.filter(is_active=True, clients__isnull=False).distinct().count()
        if owners:
            self.message_user(
                request,
                f"Kept access for {owners} PM who still own clients. Give the clients to "
                "another PM first.",
                messages.WARNING,
            )
