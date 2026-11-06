import hmac
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.accounts.selectors import has_clients_page

from . import selectors, services
from .base import TrackerError
from .models import ReleaseMove, TrackerTask, TrackerVersion


def _pm(request: HttpRequest) -> User:
    """The signed-in PM; everyone else gets "not found", as on the other PM pages."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if not has_clients_page(request.user):
        raise Http404
    return request.user


@require_GET
@login_required
def logs(request: HttpRequest) -> HttpResponse:
    """Logs · Jira sync (mockup 4.x · PM logs): the runs, what needs attention, the mapping."""
    pm = _pm(request)
    try:
        show = selectors.RunFilter(request.GET.get("show", selectors.RunFilter.ALL))
    except ValueError:
        show = selectors.RunFilter.ALL
    return render(
        request, "tracker/logs.html", {"show": show, "page": selectors.logs_page(pm, show)}
    )


# A longer number would not fit the database's id column, and the query would fail.
ID_DIGITS = 18


def _number(value: str | None) -> int | None:
    """An id from the address, or None for anything else. isdigit() alone lets "²" through,
    which int() then refuses."""
    if value and len(value) <= ID_DIGITS and value.isascii() and value.isdecimal():
        return int(value)
    return None


@require_GET
@login_required
def releases(request: HttpRequest) -> HttpResponse:
    """Releases (canvas 7 · PMReleaseView): the client's releases on the left, the chosen one's
    dates, progress, warnings and task table on the right."""
    try:
        show = selectors.TableFilter(request.GET.get("show", selectors.TableFilter.ALL))
    except ValueError:
        show = selectors.TableFilter.ALL
    try:
        plan_by = selectors.PlanBy(request.GET.get("mode", selectors.PlanBy.RELEASES))
    except ValueError:
        plan_by = selectors.PlanBy.RELEASES
    sprint = request.GET.get("sprint")
    # The note after a move shows once, on the page the move came back to.
    toast_pk = request.session.pop(UNDO_SESSION_KEY, None)
    toast = selectors.move_toast(_pm(request), toast_pk) if toast_pk else None
    page = selectors.releases_page(
        _pm(request),
        _number(request.GET.get("client")),
        _number(request.GET.get("release")),
        show,
        selectors.task_types(request.GET.get("types")),
        plan_by,
        str(_number(sprint)) if sprint != "none" else sprint,
    )
    return render(request, "tracker/releases.html", {"page": page, "toast": toast})


@require_POST
@login_required
def release_visibility(request: HttpRequest, pk: int) -> HttpResponse:
    """Hide or Show: only dimond's list changes (D-006); Jira and clients see nothing."""
    try:
        version = selectors.release_for(_pm(request), pk)
    except TrackerVersion.DoesNotExist:
        raise Http404 from None
    hidden = request.POST.get("hidden") == "1"
    services.set_release_hidden(version, hidden)
    query = urlencode({"client": request.POST.get("client", ""), "release": pk})
    return redirect(f"{reverse('releases')}?{query}")


@require_POST
@login_required
def sync_now(request: HttpRequest) -> HttpResponse:
    services.ask_for_sync(_pm(request))
    messages.success(request, "Sync started. New statuses show up here in a few seconds.")
    return redirect("logs")


@csrf_exempt
@require_POST
def jira_webhook(request: HttpRequest, token: str) -> HttpResponse:
    """Jira says a task changed (ADR 0020). Jira 8.13 cannot sign its webhooks, so the secret
    token in the address is the only proof the call comes from our Jira; the body is never
    read. The answer is instant: Jira 8.13 waits for it before it finishes saving the task."""
    expected = settings.TRACKER_JIRA_WEBHOOK_TOKEN
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        raise Http404
    key = request.GET.get("key", "")
    if not services.TASK_KEY.fullmatch(key):
        return HttpResponseBadRequest("The webhook address in Jira needs ?key=${issue.key}.")
    services.wake_for_webhook(key)
    return HttpResponse(status=204)


UNDO_SESSION_KEY = "release_move"


def _move_end(pm: User, value: str | None) -> TrackerVersion | None:
    """A release from the Move form, or None for "No release". Another PM's release, or
    anything that is not a release, is "not found"."""
    if value == selectors.NO_RELEASE:
        return None
    pk = _number(value)
    if pk is None:
        raise Http404
    try:
        return selectors.release_for(pm, pk)
    except TrackerVersion.DoesNotExist:
        raise Http404 from None


def _back_to_releases(client: str | None, version: TrackerVersion | None) -> HttpResponse:
    query = {"client": client or ""} | ({"release": str(version.pk)} if version else {})
    return redirect(f"{reverse('releases')}?{urlencode(query)}")


@require_POST
@login_required
def move_task(request: HttpRequest, key: str) -> HttpResponse:
    """A task's Move menu on the Releases page (30f; D-005): another release, or none."""
    pm = _pm(request)
    try:
        task = selectors.task_to_move(pm, key)
    except TrackerTask.DoesNotExist:
        raise Http404 from None
    source = _move_end(pm, request.POST.get("from"))
    target = _move_end(pm, request.POST.get("to"))
    back = _back_to_releases(request.POST.get("client"), source)
    if source == target:
        return back
    if source is not None and source not in task.versions.all():
        messages.info(request, f"{task.key} is no longer in {source.name}: the page was old.")
        return back
    try:
        moved = services.move_task(pm, task, source, target)
    except TrackerError as error:
        messages.error(request, f"{task.key} stays where it was. {error}")
        return back
    request.session[UNDO_SESSION_KEY] = moved.move.pk
    if moved.left_behind:
        messages.warning(
            request,
            f"Jira did not move {', '.join(moved.left_behind)} along with {task.key}: "
            "change it in Jira.",
        )
    return back


@require_POST
@login_required
def undo_move(request: HttpRequest, pk: int) -> HttpResponse:
    """Undo on the note after a move: the task goes back with the subtasks that moved along."""
    pm = _pm(request)
    try:
        move = selectors.move_for(pm, pk)
    except ReleaseMove.DoesNotExist:
        raise Http404 from None
    client = request.POST.get("client")
    if hasattr(move, "undone_by"):
        messages.info(request, f"{move.task_key} was already moved back.")
        return _back_to_releases(client, None)
    try:
        moved = services.undo_move(pm, move)
    except (TrackerTask.DoesNotExist, TrackerVersion.DoesNotExist):
        messages.error(
            request, f"{move.task_key} can't go back: the task or the release is gone from Jira."
        )
        return _back_to_releases(client, None)
    except TrackerError as error:
        messages.error(request, f"{move.task_key} stays where it is. {error}")
        return _back_to_releases(client, None)
    request.session[UNDO_SESSION_KEY] = moved.move.pk
    return _back_to_releases(client, moved.target)


@require_GET
@login_required
def logs_releases(request: HttpRequest) -> HttpResponse:
    """Logs · Releases (30f): every move of a task to another release, by whom and when."""
    return render(
        request, "tracker/logs_releases.html", {"moves": selectors.release_moves_for(_pm(request))}
    )
