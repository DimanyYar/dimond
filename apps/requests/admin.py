from django.contrib import admin
from django.db.models import Exists, OuterRef, QuerySet
from django.http import HttpRequest

from .models import CLOSED_STATUSES, Request, StatusEvent, WithdrawalRequest


class StatusEventInline(admin.TabularInline):
    model = StatusEvent
    fields = ["created_at", "from_status", "to_status", "actor"]
    readonly_fields = ["created_at", "from_status", "to_status", "actor"]
    extra = 0

    def has_add_permission(self, request: HttpRequest, obj: Request | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Request | None = None) -> bool:
        return False


def _undecided_ask() -> Exists:
    """Same rule as the card: an undecided ask on a request that is still open."""
    return Exists(
        WithdrawalRequest.objects.filter(request=OuterRef("pk"), confirmed__isnull=True).exclude(
            request__status__in=CLOSED_STATUSES
        )
    )


class AsksToWithdrawFilter(admin.SimpleListFilter):
    title = "asks to withdraw"
    parameter_name = "asks_to_withdraw"

    def lookups(self, request: HttpRequest, model_admin: admin.ModelAdmin) -> list[tuple[str, str]]:
        return [("yes", "Yes"), ("no", "No")]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Request]) -> QuerySet[Request]:
        if self.value() == "yes":
            return queryset.filter(_undecided_ask())
        if self.value() == "no":
            return queryset.exclude(_undecided_ask())
        return queryset


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    """Read-only: requests are created and moved through services, so the log stays whole.

    Until the PM inbox (#12) this list is where the PM spots a client's ask to withdraw.
    """

    list_display = [
        "title",
        "company",
        "type",
        "status",
        "asks_to_withdraw",
        "author",
        "created_at",
    ]
    list_filter = [AsksToWithdrawFilter, "company", "type", "status"]
    search_fields = ["title"]
    inlines = [StatusEventInline]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Request]:
        requests: QuerySet[Request] = super().get_queryset(request)
        return requests.annotate(asks_to_withdraw=_undecided_ask())

    @admin.display(boolean=True, description="Asks to withdraw", ordering="-asks_to_withdraw")
    def asks_to_withdraw(self, obj: Request) -> bool:
        return bool(obj.asks_to_withdraw)  # type: ignore[attr-defined]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Request | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Request | None = None) -> bool:
        return False
