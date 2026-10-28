"""The team writes questions in Jira's markup; the thread shows them formatted and safe
(PRD: Jira sync, team questions; ADR 0025). No test reaches Jira."""

import time

import pytest

from apps.tracker.base import CLIENT_TAG, tags_the_client
from apps.tracker.jira_markup import QUOTED, files_shown, to_html, to_text, without_tag

JIRA = "https://j.shakuro.com"


def html(markup: str) -> str:
    return str(to_html(markup, JIRA))


@pytest.mark.parametrize(
    ("markup", "shown"),
    [
        ("*only active* ones", "<p><strong>only active</strong> ones</p>"),
        ("an _archived_ one", "<p>an <em>archived</em> one</p>"),
        ("-old- new", "<p><del>old</del> new</p>"),
        ("+added+ text", "<p><ins>added</ins> text</p>"),
        ("*bold _and italic_*", "<p><strong>bold <em>and italic</em></strong></p>"),
        ("run {{npm *install*}}", "<p>run <code>npm *install*</code></p>"),
    ],
)
def test_the_teams_styles_are_shown_as_in_jira(markup: str, shown: str):
    assert html(markup) == shown


@pytest.mark.parametrize(
    "markup",
    [
        "2*3*4 is not bold",
        "snake_case_name stays",
        "a * b * c",
        "self-hosted - fine",
        r"\*not bold\*",
    ],
)
def test_marks_that_are_not_styles_stay_as_typed(markup: str):
    assert html(markup) == f"<p>{markup.replace('\\', '')}</p>"


def test_a_link_opens_its_address_in_a_new_tab():
    assert html("See [CRM settings|https://atlas.example.com/settings] first") == (
        '<p>See <a href="https://atlas.example.com/settings" '
        'title="https://atlas.example.com/settings" target="_blank" '
        'rel="noopener noreferrer nofollow">CRM settings</a> first</p>'
    )


def test_a_bare_address_is_a_link_and_the_sentences_full_stop_is_not_part_of_it():
    assert html("Docs: https://example.com/guide.").startswith(
        '<p>Docs: <a href="https://example.com/guide" '
    )
    assert html("Docs: https://example.com/guide.").endswith("</a>.</p>")


def test_html_written_in_a_comment_is_shown_as_text():
    shown = html("<script>alert(1)</script> and <b>bold</b>")

    assert shown == ("<p>&lt;script&gt;alert(1)&lt;/script&gt; and &lt;b&gt;bold&lt;/b&gt;</p>")


@pytest.mark.parametrize(
    "markup",
    [
        "[Click|javascript:alert(1)]",
        "[Click|data:text/html,<script>alert(1)</script>]",
        "[Click|//evil.example.com]",
    ],
)
def test_only_web_and_mail_addresses_become_links(markup: str):
    assert html(markup) == "<p>Click</p>"


def test_quotes_in_an_address_cannot_break_out_of_the_link():
    shown = html('[Click|https://example.com/"onmouseover="alert(1)]')

    assert 'href="https://example.com/&quot;onmouseover=&quot;alert(1)"' in shown
    assert '" onmouseover' not in shown


def test_a_mail_address_is_a_link():
    assert '<a href="mailto:dimon@shakuro.com"' in html("[Write to me|mailto:dimon@shakuro.com]")


def test_a_link_to_jira_itself_is_only_words_because_the_client_cannot_open_it():
    markup = "Same as [FI-2190|https://j.shakuro.com/browse/FI-2190]"

    assert html(markup) == "<p>Same as FI-2190</p>"
    assert to_text(markup, JIRA) == "Same as FI-2190"


def test_a_mention_reads_as_the_login_without_a_link():
    assert html("[~olga.p@shakuro.com] can you check?") == "<p>@olga.p can you check?</p>"


def test_pictures_and_file_links_are_shown_beside_the_text_not_in_it():
    markup = "Like this: !lead-sources.png|thumbnail! and [^spec.pdf]"

    assert html(markup) == "<p>Like this:  and </p>"
    assert files_shown(markup) == ["lead-sources.png", "spec.pdf"]


def test_paragraphs_and_line_breaks_are_kept():
    assert html("First line\nsecond line\n\nNew paragraph\\\\after break") == (
        "<p>First line<br>second line</p><p>New paragraph<br>after break</p>"
    )


def test_code_keeps_its_stars_and_brackets_exactly():
    shown = html("Try:\n{code:python}\n    x = a*b*c\n[1, 2]\n{code}\nThanks")

    assert shown == (
        '<p>Try:</p><pre tabindex="0"><code>    x = a*b*c\n[1, 2]</code></pre><p>Thanks</p>'
    )


def test_colour_and_panels_show_only_their_words():
    assert html("{color:red}*Urgent*{color} {panel:title=Note}read me{panel}") == (
        "<p><strong>Urgent</strong> read me</p>"
    )


def test_an_unknown_macro_is_shown_as_typed():
    assert html("{status:colour=Green}") == "<p>{status:colour=Green}</p>"


def test_a_long_line_of_stray_marks_is_shown_quickly():
    markup = "*a _b -c " * 4000

    started = time.monotonic()
    html(markup)

    assert time.monotonic() - started < 1


def test_styles_nested_thousands_deep_still_show_the_question():
    opening = "".join("*_-+"[i % 4] + "w " for i in range(3000))
    closing = " ".join("w" + "*_-+"[i % 4] for i in reversed(range(3000)))

    assert "<strong>w <em>w <del>w <ins>w *w" in html(opening + closing)


def test_the_plain_text_has_no_marks_for_previews():
    markup = "*Which* sources: [CRM settings|https://atlas.example.com/s], {{code}}?"

    assert (
        to_text(markup, JIRA) == "Which sources: CRM settings (https://atlas.example.com/s), code?"
    )


def test_exclamation_marks_in_code_are_words_not_pictures():
    markup = "Run {{echo !important!}}\n{code}\nflag = !set!\n{code}\nSee !screen.png!"

    assert html(markup) == (
        '<p>Run <code>echo !important!</code></p><pre tabindex="0"><code>flag = !set!</code>'
        "</pre><p>See </p>"
    )
    assert files_shown(markup) == ["screen.png"]


def test_a_broken_address_stays_words_and_does_not_stop_the_sync():
    markup = "[Docs|https://[broken]"

    assert html(markup) == "<p>Docs</p>"
    assert to_text(markup, JIRA) == "Docs"


@pytest.mark.parametrize("opener", ["{{a ", "[a ", "[^a ", "{code:x ", "{color:x "])
def test_thousands_of_brackets_that_never_close_are_read_quickly(opener: str):
    markup = opener * 8000

    started = time.monotonic()
    html(markup)
    files_shown(markup)

    assert time.monotonic() - started < 1


def test_files_are_listed_in_the_order_the_comment_shows_them():
    assert files_shown("See [^spec.pdf], then !screen.png! and [^spec.pdf]") == [
        "spec.pdf",
        "screen.png",
    ]


def test_the_client_tag_in_code_or_quotes_is_the_teams_words():
    markup = "#client Which filter? Run {{grep #client}}\n{quote}#client earlier{quote}"

    assert without_tag(markup, CLIENT_TAG) == (
        " Which filter? Run {{grep #client}}\n{quote}#client earlier{quote}"
    )


def test_a_question_after_a_quoted_line_still_asks_the_client():
    body = "bq. Earlier note\n#client What changed?"

    assert tags_the_client(QUOTED.sub("", body))
    assert not tags_the_client(QUOTED.sub("", "bq. #client What changed?"))


def test_a_table_is_shown_with_its_heading_row_and_scrolls_on_a_phone():
    shown = html("||Source||In use||\n|Web form|yes|\n|Import|no|")

    assert shown == (
        '<div class="message-table" tabindex="0"><table>'
        "<tr><th>Source</th><th>In use</th></tr>"
        "<tr><td>Web form</td><td>yes</td></tr>"
        "<tr><td>Import</td><td>no</td></tr>"
        "</table></div>"
    )


def test_a_bar_inside_a_link_or_code_is_not_a_cell_border():
    shown = html("|[CRM|https://atlas.example.com/s]|{{a|b}}|\\|x|")

    assert shown.count("<td>") == 3
    assert '<a href="https://atlas.example.com/s"' in shown
    assert "<code>a|b</code>" in shown


def test_a_row_of_brackets_that_never_close_is_read_quickly():
    markup = "|" + "[a {{b " * 8000 + "|"

    started = time.monotonic()
    html(markup)

    assert time.monotonic() - started < 1


def test_lists_keep_their_nesting_and_numbers():
    shown = html("* Web form\n** archived\n*# first\n* Import\n# one\n# two\n- dash")

    assert shown == (
        "<ul><li>Web form<ul><li>archived</li></ul><ol><li>first</li></ol></li>"
        "<li>Import</li></ul><ol><li>one</li><li>two</li></ol><ul><li>dash</li></ul>"
    )


def test_a_list_cannot_skip_levels_or_nest_without_end():
    markup = "\n".join("*" * depth + " deep" for depth in range(1, 3000))

    shown = html(markup)

    assert shown.count("<ul>") == 6
    assert "*** " not in shown


def test_a_bold_line_is_not_a_list():
    assert html("*Bold* start") == "<p><strong>Bold</strong> start</p>"


def test_quotes_headings_and_rules_have_their_own_look():
    shown = html("h2. Plan\n{quote}Earlier *note*{quote}\nbq. one line\n----\nDone")

    assert shown == (
        '<p class="message-heading">Plan</p>'
        "<blockquote><p>Earlier <strong>note</strong></p></blockquote>"
        "<blockquote><p>one line</p></blockquote><hr><p>Done</p>"
    )


def test_many_quotes_and_code_blocks_are_read_quickly():
    markup = "{quote}a{quote}{code}b{code}" * 4000 + "{quote}" * 4000

    started = time.monotonic()
    html(markup)

    assert time.monotonic() - started < 1


def test_the_plain_text_shows_lists_and_tables_simply():
    markup = "||Source||In use||\n|Web form|yes|\n\n* one\n** one-a\n# first"

    assert to_text(markup, JIRA) == (
        "Source | In use\nWeb form | yes\n\n• one\n  • one-a\n\n1. first"
    )
