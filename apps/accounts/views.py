import logging

from django.contrib import messages
from django.contrib.auth import HASH_SESSION_KEY, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import selectors, services
from .forms import (
    CLIENT_COLORS,
    AccountEmailsForm,
    AccountNameForm,
    EmailForm,
    InviteForm,
    JiraProjectForm,
    NewClientForm,
    PersonForm,
    WelcomeForm,
)
from .models import Company, Notify, Role, SignInLink, User

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
    _sign_in(request, user)
    return redirect("home")


def _sign_in(request: HttpRequest, user: User) -> None:
    login(request, user)
    # login() keeps the session of a browser already signed in as this person and empties it
    # for someone else: a new key either way gives the new device row its own session.
    request.session.cycle_key()
    session_key = request.session.session_key
    assert session_key is not None  # cycle_key() has just saved the session
    services.record_sign_in(user, session_key, request.headers.get("User-Agent", ""))


def _welcome(request: HttpRequest, token: str, invite: SignInLink) -> HttpResponse:
    form = WelcomeForm(request.POST or None, initial={"name": invite.user.name})
    if form.is_valid():
        try:
            user = services.accept_invite(
                token, form.cleaned_data["name"], Notify(form.cleaned_data["notify"])
            )
        except services.SignInLinkRejectedError:
            return _expired(request)
        _sign_in(request, user)
        return redirect("home")
    return render(request, "accounts/welcome.html", {"form": form, "invite": invite})


def _expired(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/sign_in.html", {"form": EmailForm(), "expired": True})


@require_POST
def sign_out(request: HttpRequest) -> HttpResponse:
    if request.session.session_key is not None:
        services.forget_device(request.session.session_key)
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
    request: HttpRequest,
    pm: User,
    pk: int | None,
    invite_form: InviteForm | None = None,
    new_client_form: NewClientForm | None = None,
    jira_form: JiraProjectForm | None = None,
) -> HttpResponse:
    try:
        page = selectors.clients_page(pm, pk)
    except Company.DoesNotExist:
        raise Http404 from None
    # ?invite opens the form; ?cancel=<person> and ?revoke=<person> ask in that row first.
    # All of it works without scripts, and the address can be shared or reloaded.
    if invite_form is None and "invite" in request.GET and page.chosen is not None:
        invite_form = InviteForm(company=page.chosen)
    if jira_form is None and "jira" in request.GET and page.chosen is not None:
        jira_form = JiraProjectForm(company=page.chosen)
    cancel = request.GET.get("cancel", "")
    revoke = request.GET.get("revoke", "")
    return render(
        request,
        "accounts/clients.html",
        {
            "page": page,
            "invite_form": invite_form,
            "cancel_person": int(cancel) if cancel.isdigit() else None,
            "revoke_person": int(revoke) if revoke.isdigit() else None,
            "new_client_form": new_client_form,
            "jira_form": jira_form,
            "client_colors": CLIENT_COLORS,
        },
    )


@require_GET
@login_required
def clients(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    """The PM's Clients screen: their companies and the people at the chosen one."""
    return _clients_screen(request, _pm(request), pk)


@require_POST
@login_required
def change_jira_project(request: HttpRequest, pk: int) -> HttpResponse:
    """The client's Jira project, changed from the Clients page."""
    pm = _pm(request)
    try:
        company = selectors.client_for(pm, pk)
    except Company.DoesNotExist:
        raise Http404 from None
    form = JiraProjectForm(request.POST, company=company)
    if not form.is_valid():
        return _clients_screen(request, pm, pk, jira_form=form)
    services.change_jira_project(pm, company, form.cleaned_data["jira_project"])
    messages.success(request, f"New {company.name} tasks will go to Jira {company.jira_project}.")
    return redirect("client", pk)


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


def _back(request: HttpRequest, pk: int, person_pk: int) -> HttpResponse:
    """Back where the PM acted: the person page sends back=person, the Clients list nothing."""
    if request.POST.get("back") == "person":
        return redirect("person", pk, person_pk)
    return redirect("client", pk)


@require_POST
@login_required
def resend_invite(request: HttpRequest, pk: int, person_pk: int) -> HttpResponse:
    pm, person = _person(request, pk, person_pk)
    try:
        services.resend_invite(pm, person)
    except services.NotAllowedError:
        messages.error(
            request,
            f"No new invite for {person.email}: they signed in, lost access or the invite was "
            "cancelled meanwhile.",
        )
    else:
        messages.success(request, f"A new invite is on its way to {person.email}.")
    return _back(request, pk, person_pk)


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


@require_POST
@login_required
def revoke_person(request: HttpRequest, pk: int, person_pk: int) -> HttpResponse:
    _, person = _person(request, pk, person_pk)
    if services.revoke_access([person]):
        messages.success(
            request,
            f"Access revoked for {person.name or person.email}. They are signed out on every "
            "device; their requests and replies stay.",
        )
    else:
        messages.error(request, f"{person.email} had already lost access.")
    return _back(request, pk, person_pk)


@require_POST
@login_required
def restore_person(request: HttpRequest, pk: int, person_pk: int) -> HttpResponse:
    pm, person = _person(request, pk, person_pk)
    try:
        services.restore_access(pm, person)
    except services.NotAllowedError:
        messages.error(request, f"{person.email} already has access.")
    else:
        messages.success(
            request,
            f"Access is back for {person.name or person.email}: a new invite is on its way.",
        )
    return _back(request, pk, person_pk)


@require_http_methods(["GET", "POST"])
@login_required
def new_client(request: HttpRequest) -> HttpResponse:
    """The New client window over the Clients page: the company and its first invites."""
    pm = _pm(request)
    form = NewClientForm(request.POST or None, owner=pm)
    if not form.is_valid():
        return _clients_screen(request, pm, None, new_client_form=form)
    data = form.cleaned_data
    try:
        company = services.create_client(
            pm,
            name=data["name"],
            email_domain=data["email_domain"],
            color=data["color"],
            jira_project=data["jira_project"],
            emails=data["emails"],
            notify=Notify(data["notify"]),
        )
    except (IntegrityError, ValidationError):
        # The same name or an address was saved meanwhile, e.g. the form sent twice.
        form.add_error(None, "Something here was just saved elsewhere. Check and send again.")
        return _clients_screen(request, pm, None, new_client_form=form)
    invited = len(data["emails"])
    messages.success(
        request,
        f"{company.name} is added."
        + (
            f" Invites sent to {invited} {'person' if invited == 1 else 'people'}."
            if invited
            else ""
        ),
    )
    return redirect("client", company.pk)


def _person_screen(
    request: HttpRequest, pm: User, pk: int, person_pk: int, form: PersonForm | None = None
) -> HttpResponse:
    try:
        page = selectors.person_page(pm, pk, person_pk)
    except (Company.DoesNotExist, User.DoesNotExist):
        raise Http404 from None
    person = page.row.person
    if form is None:
        form = PersonForm(initial={"name": person.name, "notify": person.notify})
    return render(
        request,
        "accounts/person.html",
        {"page": page, "form": form, "revoking": "revoke" in request.GET},
    )


@require_GET
@login_required
def person(request: HttpRequest, pk: int, person_pk: int) -> HttpResponse:
    """One person at the PM's client: profile, activity and access (PRD: person page)."""
    return _person_screen(request, _pm(request), pk, person_pk)


@require_POST
@login_required
def save_person(request: HttpRequest, pk: int, person_pk: int) -> HttpResponse:
    pm, person = _person(request, pk, person_pk)
    form = PersonForm(request.POST)
    if not form.is_valid():
        return _person_screen(request, pm, pk, person_pk, form)
    services.update_person(
        person, name=form.cleaned_data["name"], notify=Notify(form.cleaned_data["notify"])
    )
    messages.success(request, "Saved.")
    return redirect("person", pk, person_pk)


@require_POST
@login_required
def send_sign_in_link(request: HttpRequest, pk: int, person_pk: int) -> HttpResponse:
    _, person = _person(request, pk, person_pk)
    try:
        services.send_sign_in_link(person)
    except services.NotAllowedError:
        messages.error(
            request,
            f"No link for {person.email} now: they lost access, never signed in, or already "
            "got several links in the last 15 minutes.",
        )
    else:
        messages.success(request, f"A sign-in link is on its way to {person.email}.")
    return redirect("person", pk, person_pk)


@require_POST
@login_required
def sign_out_devices(request: HttpRequest, pk: int, person_pk: int) -> HttpResponse:
    _, person = _person(request, pk, person_pk)
    services.sign_out_everywhere(person)
    messages.success(
        request,
        f"{person.name or person.email} is signed out on every device. Access stays: they "
        "sign in again with an email link.",
    )
    return redirect("person", pk, person_pk)


def _me(request: HttpRequest) -> User:
    """The signed-in client or PM. The team gets "not found": they have no account page."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    try:
        return selectors.account_owner(request.user)
    except User.DoesNotExist:
        raise Http404 from None


def _account_screen(
    request: HttpRequest,
    name_form: AccountNameForm | None = None,
    emails_form: AccountEmailsForm | None = None,
) -> HttpResponse:
    page = selectors.account_page(_me(request), request.session.session_key)
    person = page.person
    is_pm = person.role == Role.PM
    emails_on = Notify.OWN if is_pm and person.notify != Notify.NONE else person.notify
    return render(
        request,
        "accounts/account.html",
        {
            "page": page,
            "is_pm": is_pm,
            "layout": "layouts/pm.html" if is_pm else "layouts/client.html",
            "name_form": name_form or AccountNameForm(initial={"name": person.name}),
            "emails_form": emails_form
            or AccountEmailsForm(initial={"notify": emails_on}, person=person),
        },
    )


@require_GET
@login_required
def account(request: HttpRequest) -> HttpResponse:
    """A person's own account: name, which emails, devices (PRD: email notifications)."""
    return _account_screen(request)


@require_POST
@login_required
def save_account_name(request: HttpRequest) -> HttpResponse:
    me = _me(request)
    form = AccountNameForm(request.POST)
    if not form.is_valid():
        return _account_screen(request, name_form=form)
    services.rename_myself(me, form.cleaned_data["name"])
    messages.success(request, "Name saved.")
    return redirect("account")


@require_POST
@login_required
def save_account_emails(request: HttpRequest) -> HttpResponse:
    me = _me(request)
    form = AccountEmailsForm(request.POST, person=me)
    if not form.is_valid():
        return _account_screen(request, emails_form=form)
    services.choose_emails(me, Notify(form.cleaned_data["notify"]))
    messages.success(request, "Email settings saved.")
    return redirect("account")


@require_POST
@login_required
def sign_out_other_devices(request: HttpRequest) -> HttpResponse:
    me = _me(request)
    session_key = request.session.session_key
    assert session_key is not None  # a signed-in person always has a session
    services.sign_out_other_devices(me, session_key)
    # The new fingerprint keeps this browser signed in. Not update_session_auth_hash: it
    # would also change this session's key, and the device list knows it by that key.
    request.session[HASH_SESSION_KEY] = me.get_session_auth_hash()
    messages.success(request, "Signed out on your other devices.")
    return redirect("account")
