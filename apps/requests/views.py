from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.accounts.models import User

from . import selectors, services
from .forms import TYPE_COPY, WHEN_NEEDED_COPY, NewRequestForm
from .models import RequestType, WhenNeeded


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
            return HttpResponse(status=201, headers={"Location": reverse("home")})
        return redirect("home")
    # The page's script swaps the invalid form in without a reload, so it needs an error code.
    status = 422 if form.errors else 200
    context = {
        "form": form,
        "type_copy": TYPE_COPY,
        "when_needed_copy": WHEN_NEEDED_COPY,
    }
    return render(request, "requests/new_request.html", context, status=status)
