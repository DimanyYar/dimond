import logging

from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import selectors, services
from .forms import EmailForm, WelcomeForm
from .models import Notify, SignInLink

SENT_TO = "sign_in_sent_to"

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def sign_in(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")
    form = EmailForm(request.POST or None)
    if form.is_valid():
        services.request_sign_in_link(form.cleaned_data["email"])
        request.session[SENT_TO] = form.cleaned_data["email"]
        return redirect("sign_in_sent")
    return render(request, "accounts/sign_in.html", {"form": form})


@require_GET
def sign_in_sent(request: HttpRequest) -> HttpResponse:
    email = request.session.get(SENT_TO)
    if email is None:
        return redirect("sign_in")
    return render(request, "accounts/sign_in_sent.html", {"email": email})


@require_http_methods(["GET", "POST"])
def sign_in_link(request: HttpRequest, token: str) -> HttpResponse:
    # Mail scanners open every link in an email. Spending the link only on a button press
    # keeps them from using it up before the person clicks.
    invite = selectors.open_invite(token)
    if invite is not None:
        return _welcome(request, token, invite)
    if request.method == "GET":
        return render(request, "accounts/sign_in_confirm.html")
    try:
        user = services.use_sign_in_link(token)
    except services.SignInLinkRejectedError:
        return _expired(request)
    login(request, user)
    return redirect("home")


def _welcome(request: HttpRequest, token: str, invite: SignInLink) -> HttpResponse:
    form = WelcomeForm(request.POST or None, initial={"name": invite.user.name})
    if form.is_valid():
        try:
            user = services.accept_invite(
                token, form.cleaned_data["name"], Notify(form.cleaned_data["notify"])
            )
        except services.SignInLinkRejectedError:
            return _expired(request)
        login(request, user)
        return redirect("home")
    return render(request, "accounts/welcome.html", {"form": form, "invite": invite})


def _expired(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/sign_in.html", {"form": EmailForm(), "expired": True})


@require_POST
def sign_out(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("sign_in")


def stale_form(request: HttpRequest, reason: str = "") -> HttpResponse:
    """Shown instead of Django's "CSRF verification failed" page (CSRF_FAILURE_VIEW).

    In dimond this nearly always means the person signed in or out in another tab: that
    replaces the browser's form token, so forms in older tabs stop working. Cleared cookies
    do the same, so the page names the likely cause without insisting on it. Nothing was
    done, so it says that and offers a way on, as whoever is signed in now.
    """
    logger.warning("Form rejected as stale: %s", reason)
    return render(request, "accounts/stale_form.html", status=403)
