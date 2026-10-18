import logging

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import selectors, services
from .forms import EmailForm, InviteForm, WelcomeForm
from .models import Company, Notify, SignInLink, User

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


def _pm(request: HttpRequest) -> User:
    """The signed-in PM. Everyone else gets "not found": the Clients pages are not theirs."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if not selectors.has_clients_page(request.user):
        raise Http404
    return request.user


def _clients_screen(
    request: HttpRequest, pm: User, pk: int | None, invite_form: InviteForm | None = None
) -> HttpResponse:
    try:
        page = selectors.clients_page(pm, pk)
    except Company.DoesNotExist:
        raise Http404 from None
    # ?invite opens the form and ?cancel=<person> the confirmation in that row: both work
    # without scripts, and the address can be shared or reloaded.
    if invite_form is None and "invite" in request.GET and page.chosen is not None:
        invite_form = InviteForm(company=page.chosen)
    cancel = request.GET.get("cancel", "")
    return render(
        request,
        "accounts/clients.html",
        {
            "page": page,
            "invite_form": invite_form,
            "cancel_person": int(cancel) if cancel.isdigit() else None,
        },
    )


@require_GET
@login_required
def clients(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    """The PM's Clients screen: their companies and the people at the chosen one."""
    return _clients_screen(request, _pm(request), pk)


@require_POST
@login_required
def invite_people(request: HttpRequest, pk: int) -> HttpResponse:
    pm = _pm(request)
    try:
        company = selectors.client_for(pm, pk)
    except Company.DoesNotExist:
        raise Http404 from None
    form = InviteForm(request.POST, company=company)
    if not form.is_valid():
        return _clients_screen(request, pm, pk, invite_form=form)
    try:
        invited = services.invite_people(
            pm, company, form.cleaned_data["emails"], Notify(form.cleaned_data["notify"])
        )
    except (IntegrityError, ValidationError):
        # Someone added one of these addresses between the check and the save, e.g. the
        # same form sent twice. Nothing was saved; the form says so like any taken address.
        form.add_error("emails", "One of these addresses was just added. Check the list again.")
        return _clients_screen(request, pm, pk, invite_form=form)
    messages.success(
        request,
        f"Invites sent to {invited} {'person' if invited == 1 else 'people'}. "
        "Each gets a personal link valid for 7 days.",
    )
    return redirect("client", pk)


def _person(request: HttpRequest, pk: int, person_pk: int) -> tuple[User, User]:
    pm = _pm(request)
    try:
        return pm, selectors.person_at_client(pm, pk, person_pk)
    except (Company.DoesNotExist, User.DoesNotExist):
        raise Http404 from None


@require_POST
@login_required
def resend_invite(request: HttpRequest, pk: int, person_pk: int) -> HttpResponse:
    pm, person = _person(request, pk, person_pk)
    try:
        services.resend_invite(pm, person)
    except services.NotAllowedError:
        messages.error(request, f"{person.email} has signed in or lost access meanwhile.")
    else:
        messages.success(request, f"A new invite is on its way to {person.email}.")
    return redirect("client", pk)


@require_POST
@login_required
def cancel_invite(request: HttpRequest, pk: int, person_pk: int) -> HttpResponse:
    _, person = _person(request, pk, person_pk)
    try:
        services.cancel_invite(person)
    except services.NotAllowedError:
        messages.error(request, f"{person.email} has signed in meanwhile, so the invite stays.")
    else:
        messages.success(request, f"Invite to {person.email} cancelled. The link no longer works.")
    return redirect("client", pk)
