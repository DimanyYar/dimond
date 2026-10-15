from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.accounts.models import User

from . import selectors, services
from .forms import TYPE_COPY, WHEN_NEEDED_COPY, MyRequestsFilter, NewRequestForm, View
from .models import RequestType, WhenNeeded
from .selectors import Period, Show


@require_GET
@login_required
def my_requests(request: HttpRequest) -> HttpResponse:
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if not selectors.has_my_requests_page(request.user):
        raise PermissionDenied
    filters = MyRequestsFilter(request.GET)
    show, types, period = filters.requests_filter()
    view = View(filters.chosen("view", View.BOARD))
    page = selectors.my_requests_page(
        request.user,
        show=show,
        types=types,
        period=period,
        with_board=view == View.BOARD,
        now=timezone.now(),
    )
    context = {
        "page": page,
        "show": show,
        "types_chosen": types,
        "period": period,
        "view": view,
        "shows": Show.choices,
        "type_chips": [
            {
                "value": value,
                "label": label,
                "on": value in types,
                # The link a chip leads to: the same filters with this type switched on or off.
                "toggled": [t for t in types if t != value] if value in types else [*types, value],
            }
            for value, label in RequestType.choices
        ],
        "periods": Period.choices,
    }
    return render(request, "requests/my_requests.html", context)


@require_GET
@login_required
def my_requests_count(request: HttpRequest) -> HttpResponse:
    """The label of the filter sheet's button, e.g. "Show 3 requests", for filters not yet
    applied. The sheet asks for it each time a type or period is picked."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if not selectors.has_my_requests_page(request.user):
        raise PermissionDenied
    show, types, period = MyRequestsFilter(request.GET).requests_filter()
    count = selectors.my_requests_count(
        request.user, show=show, types=types, period=period, now=timezone.now()
    )
    return render(request, "requests/show_requests_label.html", {"count": count})


@require_http_methods(["GET", "POST"])
@login_required
def new_request(request: HttpRequest) -> HttpResponse:
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if not selectors.can_create_requests(request.user):
        raise PermissionDenied
    form = NewRequestForm(request.POST or None)
    if form.is_valid():
        data = form.cleaned_data
        created = services.create_request(
            request.user,
            RequestType(data["type"]),
            data["title"],
            data["why"],
            WhenNeeded(data["when_needed"]),
            description=data["description"],
            steps=data["steps"],
            link=data["link"],
        )
        messages.success(request, f"{created.key} · {created.title}")
        if request.headers.get("X-Requested-With") == "fetch":
            # Following a redirect inside the script would use up the confirmation unseen,
            # so the script gets the address and opens it itself.
            return HttpResponse(status=201, headers={"Location": reverse("my_requests")})
        return redirect("my_requests")
    # The page's script swaps the invalid form in without a reload, so it needs an error code.
    status = 422 if form.errors else 200
    context = {
        "form": form,
        "type_copy": TYPE_COPY,
        "when_needed_copy": WHEN_NEEDED_COPY,
    }
    return render(request, "requests/new_request.html", context, status=status)
