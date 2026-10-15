from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.models import User

from . import selectors, services
from .forms import NewRequestForm
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
        return redirect("home")
    status = 422 if form.errors else 200
    return render(request, "requests/new_request.html", {"form": form}, status=status)
