"""The team's Jira markup, formatted for the thread (ADR 0025)."""

from django import template
from django.conf import settings
from django.utils.safestring import SafeString

from apps.tracker.jira_markup import to_html

register = template.Library()


@register.filter
def jira_formatted(markup: str) -> SafeString:
    return to_html(markup, settings.TRACKER_JIRA_URL)
