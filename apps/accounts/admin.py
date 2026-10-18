from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from . import services
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


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "email_domain", "created_at"]
    list_filter = ["owner"]
    search_fields = ["name", "email_domain"]
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
