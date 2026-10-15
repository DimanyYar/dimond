from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

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
    list_display = ["name", "email_domain", "created_at"]
    search_fields = ["name", "email_domain"]
    inlines = [PeopleInline]


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ["email", "name", "role", "company", "is_active", "last_login"]
    list_filter = ["role", "company", "is_active"]
    search_fields = ["email", "name"]
    fields = [
        "email",
        "name",
        "role",
        "company",
        "is_active",
        "is_staff",
        "date_joined",
        "last_login",
    ]
    readonly_fields = ["date_joined", "last_login"]

    def get_queryset(self, request: HttpRequest) -> QuerySet[User]:
        return super().get_queryset(request).select_related("company")
