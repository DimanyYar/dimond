"""Jira's wiki markup in the team's comments, read by dimond itself (ADR 0025).

The client sees a team question formatted as the team wrote it, and as plain text where a
line of it is enough (previews, the quote in their answer). Jira's own HTML is never taken:
every tag here is one dimond chose, every word of the comment is escaped, and only web and
mail addresses become links.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

# Pictures and file links: the files are shown beside the text, not inside it.
PICTURE = re.compile(r"!([^!\s|][^!|\n]*?)(?:\|[^!\n]*)?!")
FILE_LINK = re.compile(r"\[\^([^\]\n]+)\]")
# Code and noformat keep their text exactly: a star in code is a star.
# Not inside {{…}}, which is code in a line, not a block.
VERBATIM = re.compile(
    r"(?<!\{)\{(code|noformat)(?::[^}\n]*)?\}(?!\})\n?(.*?)(?:\{\1\}|\Z)", re.DOTALL
)
# Shown by their contents only: colour, panels, and quotes until quotes get their own look.
WRAPPER_MACRO = re.compile(r"\{(?:quote|panel|color)(?::[^}\n]*)?\}")
BLANK_LINES = re.compile(r"\n[ \t]*\n")

INLINE = re.compile(
    r"(?P<br>\\\\)"
    r"|\\(?P<escaped>[^\s\\])"
    r"|\{\{(?P<code>.+?)\}\}"
    r"|\[(?P<bracket>[^\]\n]+)\]"
    r"|(?P<url>(?<![\w/])(?:https?://|mailto:)[^\s\[\]|<>\"]+)"
    r"|(?P<mark>[*_+-])"
)
STYLES = {"*": "strong", "_": "em", "-": "del", "+": "ins"}
# A sentence's last character after an address belongs to the sentence.
URL_TAIL = re.compile(r"[.,;:!?)]+$")
LINK_SCHEMES = ("http", "https", "mailto")
# Deeper than this, marks are shown as typed: no real comment nests more styles, and a
# made-up one cannot run the reader out of stack.
MAX_DEPTH = 4


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Styled:
    tag: str
    children: tuple["Inline", ...]


@dataclass(frozen=True)
class Code:
    text: str


@dataclass(frozen=True)
class Link:
    children: tuple["Inline", ...]
    url: str


@dataclass(frozen=True)
class Break:
    pass


Inline = Text | Styled | Code | Link | Break
# A paragraph is lines of inline pieces; a verbatim one keeps its lines as they are.
Paragraph = tuple[tuple[Inline, ...], ...]


def files_shown(body: str) -> list[str]:
    """Names of the files a comment shows or links to, in order, each once."""
    shown: list[str] = []
    for match in (*PICTURE.finditer(body), *FILE_LINK.finditer(body)):
        name = match.group(1).strip()
        if name not in shown:
            shown.append(name)
    return shown


def to_html(markup: str, jira_address: str) -> SafeString:
    """The comment as the thread shows it. Links to Jira itself stay words: the client has no
    access there."""
    rendered = "".join(
        "<p>" + "<br>".join(_html(line, jira_address) for line in paragraph) + "</p>"
        for paragraph in _paragraphs(markup)
    )
    # Safe: every word of the comment went through escape(), every tag is one written here.
    return mark_safe(rendered)  # noqa: S308


def to_text(markup: str, jira_address: str = "") -> str:
    """The comment as plain words: styles gone, a link as its words and address, a mention as
    @login."""
    return "\n\n".join(
        "\n".join(_text(line, jira_address) for line in paragraph)
        for paragraph in _paragraphs(markup)
    )


def _paragraphs(markup: str) -> list[Paragraph]:
    markup = PICTURE.sub("", markup.replace("\r\n", "\n"))
    paragraphs: list[Paragraph] = []
    position = 0
    for block in VERBATIM.finditer(markup):
        paragraphs += _formatted(markup[position : block.start()])
        lines = block.group(2).rstrip().split("\n")
        if any(line.strip() for line in lines):
            paragraphs.append(tuple((Text(line.rstrip()),) for line in lines))
        position = block.end()
    return paragraphs + _formatted(markup[position:])


def _formatted(markup: str) -> list[Paragraph]:
    markup = WRAPPER_MACRO.sub("", markup)
    paragraphs = []
    for chunk in BLANK_LINES.split(markup):
        lines = [line.strip() for line in chunk.split("\n") if line.strip()]
        if lines:
            paragraphs.append(tuple(_inline(line) for line in lines))
    return paragraphs


def _inline(line: str, depth: int = 0) -> tuple[Inline, ...]:
    pieces: list[Inline] = []
    closers = _closers(line)
    plain_from = position = 0
    while (match := INLINE.search(line, position)) is not None:
        found = _piece(match, line, closers, depth)
        if found is None:
            position = match.start() + 1
            continue
        piece, position = found
        if match.start() > plain_from:
            pieces.append(Text(line[plain_from : match.start()]))
        if piece is not None:
            pieces.append(piece)
        plain_from = position
    if plain_from < len(line):
        pieces.append(Text(line[plain_from:]))
    return tuple(pieces)


def _piece(
    match: re.Match[str], line: str, closers: dict[str, list[int]], depth: int
) -> tuple[Inline | None, int] | None:
    """What the match is and where the text continues, or None when it is plain text after
    all. A piece of None is markup that shows nothing, like a file link."""
    kind = match.lastgroup
    if kind == "br":
        return Break(), match.end()
    if kind == "escaped":
        return Text(match.group("escaped")), match.end()
    if kind == "code":
        return Code(match.group("code")), match.end()
    if kind == "bracket":
        return _bracket(match.group("bracket"), depth), match.end()
    if kind == "url":
        url = URL_TAIL.sub("", match.group("url"))
        return Link((Text(url),), url), match.start() + len(url)
    return _styled(line, match.start(), closers, depth)


def _closers(line: str) -> dict[str, list[int]]:
    """Where each mark may close a style: after a non-space, not followed by a letter or the
    same mark, the way Jira reads *bold*. Found once per line, so a long line of stray stars
    stays fast."""
    closers: dict[str, list[int]] = {mark: [] for mark in STYLES}
    for index, char in enumerate(line):
        if char not in STYLES or index == 0 or line[index - 1].isspace():
            continue
        after = line[index + 1 : index + 2]
        if not after or (not after.isalnum() and after != char):
            closers[char].append(index)
    return closers


def _styled(
    line: str, start: int, closers: dict[str, list[int]], depth: int
) -> tuple[Inline, int] | None:
    mark = line[start]
    before = line[start - 1 : start]
    after = line[start + 1 : start + 2]
    if before.isalnum() or before == mark or not after or after.isspace() or after == mark:
        return None
    candidates = closers[mark]
    found = bisect_right(candidates, start + 1)
    if depth >= MAX_DEPTH or found == len(candidates):
        return None
    end = candidates[found]
    return Styled(STYLES[mark], _inline(line[start + 1 : end], depth + 1)), end + 1


def _bracket(inside: str, depth: int) -> Inline | None:
    """[~login] is a mention, [^file] a file shown beside the text, [words|address] and
    [address] are links; anything else is the words as typed, brackets included."""
    if inside.startswith("~"):
        return Text("@" + inside[1:].split("@")[0])
    if inside.startswith("^"):
        return None
    words, bar, address = inside.partition("|")
    if bar:
        return Link(_inline(words.strip(), depth + 1), address.strip())
    if inside.strip().lower().startswith(("http://", "https://", "mailto:")):
        return Link((Text(inside.strip()),), inside.strip())
    return Text(f"[{inside}]")


def _linkable(url: str, jira_address: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme.lower() not in LINK_SCHEMES:
        return False
    jira_host = urlsplit(jira_address).hostname
    return parts.hostname is None or parts.hostname != jira_host


def _html(pieces: tuple[Inline, ...], jira_address: str) -> str:
    return "".join(_html_piece(piece, jira_address) for piece in pieces)


def _html_piece(piece: Inline, jira_address: str) -> str:
    match piece:
        case Text(text):
            return str(escape(text))
        case Code(text):
            return f"<code>{escape(text)}</code>"
        case Break():
            return "<br>"
        case Styled(tag, children):
            return f"<{tag}>{_html(children, jira_address)}</{tag}>"
        case Link(children, url) if _linkable(url, jira_address):
            words = _html(children, jira_address)
            return (
                f'<a href="{escape(url)}" title="{escape(url)}" target="_blank" '
                f'rel="noopener noreferrer nofollow">{words}</a>'
            )
        case Link(children, _):
            return _html(children, jira_address)


def _text(pieces: tuple[Inline, ...], jira_address: str) -> str:
    return "".join(_text_piece(piece, jira_address) for piece in pieces)


def _text_piece(piece: Inline, jira_address: str) -> str:
    match piece:
        case Text(text) | Code(text):
            return text
        case Break():
            return "\n"
        case Styled(_, children):
            return _text(children, jira_address)
        case Link(children, url):
            words = _text(children, jira_address)
            if words == url or not _linkable(url, jira_address):
                return words
            return f"{words} ({url})"
