from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpRequest

from .models import Company, User


class PeopleInline(admin.TabularInline):
    model = User
    fields = ["email", "name", "is_active"]
    extra = 0
    show_change_link = True


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

    def save_model(
        self, request: HttpRequest, obj: User, form: ModelForm[User], change: bool
    ) -> None:
        # People added here sign in by email link only (ADR 0003).
        if not change:
            obj.set_unusable_password()
        super().save_model(request, obj, form, change)

    def get_queryset(self, request: HttpRequest) -> QuerySet[User]:
        return super().get_queryset(request).select_related("company")
