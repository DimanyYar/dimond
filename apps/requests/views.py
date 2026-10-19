from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import User
from apps.tracker.base import TrackerError, TrackerUnreachable

from . import selectors, services
from .forms import (
    TYPE_COPY,
    WHEN_NEEDED_COPY,
    DeclineForm,
    EditRequestForm,
    JiraTaskForm,
    LinkJiraTaskForm,
    MessageForm,
    MyRequestsFilter,
    NewRequestForm,
    TriageForm,
    View,
    VoiceRequestForm,
    WithdrawalDecisionForm,
    WithdrawForm,
)
from .models import FORM_TYPES, Request, RequestType, WhenNeeded
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
    query = filters.chosen("q")
    page = selectors.my_requests_page(
        request.user,
        show=show,
        types=types,
        period=period,
        with_board=view == View.BOARD,
        now=timezone.now(),
        company=filters.chosen_company(),
        query=query,
    )
    context = {
        "page": page,
        "is_client": selectors.is_client(request.user),
        "company": filters.chosen_company(),
        # Cards name their client when the PM looks at every client at once.
        "show_client": not selectors.is_client(request.user) and filters.chosen_company() is None,
        "query": query,
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
            for value, label in FORM_TYPES
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
    filters = MyRequestsFilter(request.GET)
    show, types, period = filters.requests_filter()
    count = selectors.my_requests_count(
        request.user,
        show=show,
        types=types,
        period=period,
        now=timezone.now(),
        company=filters.chosen_company(),
    )
    return render(request, "requests/show_requests_label.html", {"count": count})


@require_http_methods(["GET", "POST"])
@login_required
def new_request(request: HttpRequest) -> HttpResponse:
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if not selectors.can_create_requests(request.user):
        raise PermissionDenied
    form = NewRequestForm(request.POST or None, request.FILES or None)
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
            files=data["attachments"],
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
        # Sent files are dropped when the form comes back with errors; this asks for them again.
        "reattach": bool(form.errors and request.FILES),
        "type_copy": TYPE_COPY,
        "when_needed_copy": WHEN_NEEDED_COPY,
        "voice_types": FORM_TYPES,
    }
    return render(request, "requests/new_request.html", context, status=status)


def _card_page(request: HttpRequest, pk: int) -> selectors.RequestPage:
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    try:
        return selectors.request_page(request.user, pk)
    except Request.DoesNotExist:
        raise Http404 from None


def _render_card(
    request: HttpRequest,
    page: selectors.RequestPage,
    form: MessageForm | None = None,
    withdraw_form: WithdrawForm | None = None,
    decline_form: DeclineForm | None = None,
    jira_form: JiraTaskForm | None = None,
    link_form: LinkJiraTaskForm | None = None,
    status: int = 200,
) -> HttpResponse:
    """The card with whichever of its forms was just sent, errors and all."""
    context = {
        "page": page,
        "form": form or MessageForm(),
        # Sent files are dropped when the form comes back with errors; this asks for them again.
        "reattach": bool(form and form.errors and request.FILES),
        "withdraw_form": withdraw_form or WithdrawForm(reason_required=page.can_ask_to_withdraw),
        "decline_form": decline_form or DeclineForm(),
        "jira_form": jira_form,
        "link": _link_panel(request, page, link_form) if link_form else None,
        # Picks the client's or the PM's navigation and words.
        "is_client": selectors.is_client(page.viewer),
    }
    return render(request, "requests/request_detail.html", context, status=status)


@require_POST
@login_required
def new_voice_request(request: HttpRequest) -> HttpResponse:
    """ "Just say it" sends the recording from its own window by script; it gets the address to
    open, or the problems to show in that window."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if not selectors.can_create_requests(request.user):
        raise PermissionDenied
    form = VoiceRequestForm(request.POST, request.FILES)
    if not form.is_valid():
        problems = [str(error) for errors in form.errors.values() for error in errors]
        return JsonResponse({"errors": problems}, status=422)
    created = services.create_voice_request(
        request.user,
        form.cleaned_data["title"],
        form.cleaned_data["recording"],
        form.request_type(),
        form.cleaned_data["text"],
    )
    messages.success(request, f"{created.key} · {created.title}")
    return HttpResponse(status=201, headers={"Location": reverse("my_requests")})


@require_http_methods(["GET", "POST"])
@login_required
def request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """The request card with its thread; posting the form adds a message."""
    page = _card_page(request, pk)
    if request.method == "GET":
        # The page was read first, so it still knows what was new before this visit.
        services.mark_seen(page.viewer, page.request, page.read_at)
        return _render_card(request, page)
    form = MessageForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render_card(request, page, form=form, status=422)
    if form.is_note():
        try:
            note = services.add_note(page.viewer, page.request, form.cleaned_data["text"])
        except services.NotAllowedError:
            raise PermissionDenied from None
        return redirect(f"{page.request.get_absolute_url()}#note-{note.pk}")
    try:
        message = services.post_message(
            page.viewer,
            page.request,
            form.cleaned_data["text"],
            ask_for_input=form.cleaned_data["ask_for_input"],
            files=form.cleaned_data["attachments"],
        )
    except services.NotAllowedError:
        raise PermissionDenied from None
    return redirect(f"{page.request.get_absolute_url()}#message-{message.pk}")


@require_POST
@login_required
def request_withdraw(request: HttpRequest, pk: int) -> HttpResponse:
    """The client's Withdraw: closes the request at once, or asks the PM with a reason."""
    page = _card_page(request, pk)
    if not (page.can_withdraw_now or page.can_ask_to_withdraw):
        raise PermissionDenied
    form = WithdrawForm(request.POST, reason_required=page.can_ask_to_withdraw)
    if not form.is_valid():
        return _render_card(request, page, withdraw_form=form, status=422)
    try:
        services.withdraw_request(page.viewer, page.request, form.cleaned_data["reason"])
    except services.NotAllowedError:
        raise PermissionDenied from None
    return redirect(page.request.get_absolute_url())


@require_POST
@login_required
def request_withdrawal_decision(request: HttpRequest, pk: int) -> HttpResponse:
    """The PM confirms the client's ask to withdraw, or keeps the request going."""
    page = _card_page(request, pk)
    form = WithdrawalDecisionForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest()
    try:
        services.decide_withdrawal(
            page.viewer, page.request, confirm=form.cleaned_data["decision"] == "confirm"
        )
    except services.NotAllowedError:
        raise PermissionDenied from None
    return redirect(page.request.get_absolute_url())


TRIAGE_ACTIONS = {
    "later": services.move_to_later,
    "back": services.bring_back,
    "handled": services.mark_handled,
}


@require_POST
@login_required
def request_triage(request: HttpRequest, pk: int) -> HttpResponse:
    """The PM's one-click actions: Move to Later, Bring back, No reply needed."""
    page = _card_page(request, pk)
    form = TriageForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest()
    try:
        TRIAGE_ACTIONS[form.cleaned_data["action"]](page.viewer, page.request)
    except services.NotAllowedError:
        raise PermissionDenied from None
    return redirect(page.request.get_absolute_url())


@require_POST
@login_required
def request_decline(request: HttpRequest, pk: int) -> HttpResponse:
    """The PM declines the request; the reason goes to the client in the thread."""
    page = _card_page(request, pk)
    if not page.can_decline:
        raise PermissionDenied
    form = DeclineForm(request.POST)
    if not form.is_valid():
        return _render_card(request, page, decline_form=form, status=422)
    try:
        services.decline_request(page.viewer, page.request, form.cleaned_data["reason"])
    except services.NotAllowedError:
        raise PermissionDenied from None
    return redirect(page.request.get_absolute_url())


@require_http_methods(["GET", "POST"])
@login_required
def request_jira_task(request: HttpRequest, pk: int) -> HttpResponse:
    """The Create Jira task window over the card; sending it creates the task and plans the
    request. A Jira refusal comes back in the window with the PM's text kept."""
    page = _card_page(request, pk)
    if not page.can_create_jira_task:
        raise PermissionDenied
    if request.method == "GET" and request.GET.get("mode") == "existing":
        return _render_card(request, page, link_form=LinkJiraTaskForm())
    form = JiraTaskForm(request.POST or None, request=page.request)
    if not form.is_valid():
        return _render_card(request, page, jira_form=form, status=422 if form.is_bound else 200)
    data = form.cleaned_data
    try:
        planned = services.create_jira_task(
            page.viewer,
            page.request,
            issue_type=data["issue_type"],
            summary=data["summary"],
            description=data["description"],
            reply=form.message_for_client(),
        )
    except services.NotAllowedError:
        raise PermissionDenied from None
    except TrackerUnreachable as error:
        # The connection broke, maybe after Jira made the task: a blind retry could make two.
        form.add_error(
            None,
            f"{error} Jira may still have created the task: search "
            f"{page.request.company.jira_project} for {page.request.key} before trying again.",
        )
        return _render_card(request, page, jira_form=form, status=422)
    except TrackerError as error:
        form.add_error(None, str(error))
        return _render_card(request, page, jira_form=form, status=422)
    # The thread's new line "Planned · FI-2301" is the confirmation: the card shows no banners.
    return redirect(planned.get_absolute_url())


def _link_panel(
    request: HttpRequest, page: selectors.RequestPage, form: LinkJiraTaskForm
) -> dict[str, object]:
    """What the Existing task tab shows: the search, its results and the form to link."""
    query = request.GET.get("q", "").strip()
    try:
        tasks, problem = selectors.tasks_to_link(page.viewer, page.request, query), ""
    except TrackerError as error:
        tasks, problem = [], f"Couldn't load the tasks: {error}"
    return {"form": form, "query": query, "tasks": tasks, "problem": problem}


@require_POST
@login_required
def request_link_jira_task(request: HttpRequest, pk: int) -> HttpResponse:
    """Existing task: links the chosen task and plans the request, or says why it can't."""
    page = _card_page(request, pk)
    if not page.can_create_jira_task:
        raise PermissionDenied
    form = LinkJiraTaskForm(request.POST)
    if not form.is_valid():
        return _render_card(request, page, link_form=form, status=422)
    try:
        services.link_jira_task(
            page.viewer, page.request, form.cleaned_data["key"], form.message_for_client()
        )
    except services.NotAllowedError:
        raise PermissionDenied from None
    except (services.TaskNotLinkable, TrackerError) as error:
        form.add_error("key", str(error))
        return _render_card(request, page, link_form=form, status=422)
    return redirect(page.request.get_absolute_url())


@require_http_methods(["GET", "POST"])
@login_required
def request_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """The request form filled with the request's text; saving it goes back to the card."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    # Whoever sends requests may edit them; for them the only "no" left is the status.
    if not selectors.can_create_requests(request.user):
        raise PermissionDenied
    try:
        page = selectors.edit_page(request.user, pk)
    except Request.DoesNotExist:
        raise Http404 from None
    card = page.request.get_absolute_url()
    editable = selectors.can_edit_request(request.user, page.request)
    if request.method == "GET" and not editable:
        # The card shows the status and no Edit button, which says why better than an error.
        return redirect(card)
    form = EditRequestForm.for_request(
        page.request, page.files, request.POST or None, request.FILES or None
    )
    if editable and form.is_valid():
        data = form.cleaned_data
        try:
            services.edit_request(
                request.user,
                page.request,
                RequestType(data["type"]),
                data["title"],
                data["why"],
                WhenNeeded(data["when_needed"]),
                description=data["description"],
                steps=data["steps"],
                link=data["link"],
                add_files=data["attachments"],
                remove_file_ids=data["remove"],
            )
        except services.NotAllowedError:
            editable = False  # Planned while the form was open
        else:
            return redirect(f"{card}#request")
    context = {
        "page": page,
        "form": form,
        "editable": editable,
        "reattach": bool(form.errors and request.FILES),
        "type_copy": TYPE_COPY,
        "when_needed_copy": WHEN_NEEDED_COPY,
    }
    # 409 when the request moved on while the form was open: the typed text is shown again
    # so it can be copied into the thread.
    status = 200 if request.method == "GET" else (422 if editable else 409)
    return render(request, "requests/request_edit.html", context, status=status)
