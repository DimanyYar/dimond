"""Jira's wiki markup in the team's comments, read by dimond itself (ADR 0025).

The client sees a team question formatted as the team wrote it, and as plain text where a
line of it is enough (previews, the quote in their answer). Jira's own HTML is never taken:
every tag here is one dimond chose, every word of the comment is escaped, and only web and
mail addresses become links.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass
from itertools import groupby, pairwise
from urllib.parse import urlsplit

from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

# Every bracketed piece has a length limit: without one, a line of thousands of "[" or "{{"
# that never close is read again from each of them, and one such comment would hold up the
# sync for seconds. Longer pieces are shown as typed; real links and code fit easily.
LONGEST = 500
# Pictures and file links: the files are shown beside the text, not inside it. In code,
# "!flag!" is words, not a picture.
PICTURE = re.compile(r"!([^!\s|][^!|\n]*?)(?:\|[^!\n]*)?!")
FILE_LINK = re.compile(rf"\[\^([^\]\n]{{1,{LONGEST}}})\]")
CODE_IN_LINE = re.compile(rf"\{{\{{(?P<code>[^\n]{{1,{LONGEST}}}?)\}}\}}")
# Code and noformat keep their text exactly: a star in code is a star.
# Not inside {{…}}, which is code in a line, not a block.
VERBATIM = re.compile(
    rf"(?<!\{{)\{{(code|noformat)(?::[^}}\n]{{0,{LONGEST}}})?\}}(?!\}})\n?(.*?)(?:\{{\1\}}|\Z)",
    re.DOTALL,
)
# Quoted text, whole: {quote}…{quote} and "bq." lines. A #client tag inside one is dimond's
# own answer quoting the question, not a new question.
# A "bq." quote ends with its line: what follows may be a new question.
QUOTED = re.compile(r"\{quote\}.*?(?:\{quote\}|\Z)|^bq\. [^\n]*$", re.DOTALL | re.MULTILINE)
# Where code blocks and quotes open and close; which of them count is decided in _blocks.
BLOCK_MACRO = re.compile(
    rf"(?<!\{{)\{{(?P<kind>code|noformat|quote)(?::[^}}\n]{{0,{LONGEST}}})?\}}(?!\}})"
)
# Shown by their contents only: colour and panels.
WRAPPER_MACRO = re.compile(rf"\{{(?:panel|color)(?::[^}}\n]{{0,{LONGEST}}})?\}}")
HEADING = re.compile(r"h[1-6]\. +(.*)")
RULE = re.compile(r"-{4,}")
QUOTE_LINE = re.compile(r"bq\. +(.*)")
# "* item", "## item", "*# item", "- item"; a mark needs a space after it, so *bold* is not one.
LIST_ITEM = re.compile(r"([*#]+|-) +(\S.*)")
# "4. Request ID": a point the team numbered by hand. Two digits at most, so "2026. was…" is
# not one.
POINT = re.compile(r"(\d{1,2})\. +(\S.*)")
# Deeper lists are shown at this depth: enough for any real comment, and the reader's
# recursion stays short whatever is typed.
MAX_LIST_DEPTH = 6

INLINE = re.compile(
    r"(?P<br>\\\\)"
    r"|\\(?P<escaped>[^\s\\])"
    rf"|{CODE_IN_LINE.pattern}"
    rf"|(?P<picture>{PICTURE.pattern})"
    rf"|\[(?P<bracket>[^\]\n]{{1,{LONGEST}}})\]"
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
Line = tuple[Inline, ...]


@dataclass(frozen=True)
class Point:
    """A line the team numbered by hand: a line of its paragraph, only shown differently."""

    number: str
    line: Line


@dataclass(frozen=True)
class Paragraph:
    lines: tuple[Line | Point, ...]


@dataclass(frozen=True)
class Heading:
    line: Line


@dataclass(frozen=True)
class ListItem:
    line: Line
    lists: tuple["ListBlock", ...]


@dataclass(frozen=True)
class ListBlock:
    ordered: bool
    items: tuple[ListItem, ...]


@dataclass(frozen=True)
class Cell:
    header: bool
    line: Line


@dataclass(frozen=True)
class Table:
    rows: tuple[tuple[Cell, ...], ...]


@dataclass(frozen=True)
class CodeBlock:
    text: str


@dataclass(frozen=True)
class Quote:
    blocks: tuple["Block", ...]


@dataclass(frozen=True)
class Rule:
    pass


Block = Paragraph | Heading | ListBlock | Table | CodeBlock | Quote | Rule


def files_shown(body: str) -> list[str]:
    """Names of the files a comment shows or links to, in order, each once."""
    shown: list[str] = []
    outside_code = CODE_IN_LINE.sub("", VERBATIM.sub("", body))
    found = (*PICTURE.finditer(outside_code), *FILE_LINK.finditer(outside_code))
    for match in sorted(found, key=lambda match: match.start()):
        name = match.group(1).strip()
        if name not in shown:
            shown.append(name)
    return shown


def without_tag(markup: str, tag: re.Pattern[str]) -> str:
    """The markup with the tag taken out where it tags. In code and quotes it is the team's
    words ("grep #client") and stays."""
    kept = [
        found.span()
        for words in (VERBATIM, QUOTED, CODE_IN_LINE)
        for found in words.finditer(markup)
    ]
    return tag.sub(
        lambda match: match.group() if any(a <= match.start() < b for a, b in kept) else "",
        markup,
    )


def to_html(markup: str, jira_address: str) -> SafeString:
    """The comment as the thread shows it. Links to Jira itself stay words: the client has no
    access there."""
    rendered = "".join(_block_html(block, jira_address) for block in _read(markup))
    # Safe: every word of the comment went through escape(), every tag is one written here.
    return mark_safe(rendered)  # noqa: S308


def to_text(markup: str, jira_address: str = "") -> str:
    """The comment as plain words: styles gone, a link as its words and address, a mention as
    @login, list items with bullets, table cells between bars."""
    texts = (_block_text(block, jira_address) for block in _read(markup))
    return "\n\n".join(text for text in texts if text)


def _read(markup: str) -> list[Block]:
    return _blocks(markup.replace("\r\n", "\n"))


def _blocks(markup: str) -> list[Block]:
    """Code blocks and quotes first, as their lines are not read like the others; the rest
    line by line. Their markers are found in one pass; one escaped with "\\" or inside
    {{…}} is words, whether it opens or closes."""
    in_line_code = [found.span() for found in CODE_IN_LINE.finditer(markup)]
    macros = [
        found
        for found in BLOCK_MACRO.finditer(markup)
        if not _escaped(markup, found.start()) and not _within(found.start(), in_line_code)
    ]
    blocks: list[Block] = []
    position = index = 0
    while index < len(macros):
        opening = macros[index]
        closing = _closing(macros, index)
        inside_ends = macros[closing].start() if closing is not None else len(markup)
        blocks += _lines(markup[position : opening.start()])
        blocks += _enclosed(opening["kind"], markup[opening.end() : inside_ends])
        position = macros[closing].end() if closing is not None else len(markup)
        index = closing + 1 if closing is not None else len(macros)
    return blocks + _lines(markup[position:])


def _closing(macros: list[re.Match[str]], opening: int) -> int | None:
    """The marker that closes the one at `opening`, or None when it runs to the end. Inside a
    code block other markers are its words; inside a quote a code block is skipped whole,
    as a {quote} in it is code. Every marker is looked at once."""
    kind = macros[opening]["kind"]
    index = opening + 1
    while index < len(macros):
        current = macros[index]["kind"]
        if current == kind:
            return index
        if kind == "quote":
            inner = _closing(macros, index)
            if inner is None:
                return None
            index = inner
        index += 1
    return None


def _escaped(markup: str, index: int) -> bool:
    # Two backslashes before a macro are Jira's line break, not an escape: only an odd count
    # escapes it.
    before = index
    while before > 0 and markup[before - 1] == "\\":
        before -= 1
    return (index - before) % 2 == 1


def _within(index: int, spans: list[tuple[int, int]]) -> bool:
    found = bisect_right(spans, index, key=lambda span: span[0]) - 1
    return found >= 0 and index < spans[found][1]


def _enclosed(kind: str, inside: str) -> list[Block]:
    if kind == "quote":
        blocks = tuple(_blocks(inside))
        return [Quote(blocks)] if blocks else []
    # The line break after {code} and the empty lines before the closing one are how the
    # comment was laid out in Jira; anything else, trailing spaces included, is kept.
    text = inside.removeprefix("\n").rstrip("\n")
    return [CodeBlock(text)] if text.strip() else []


def _lines(markup: str) -> list[Block]:
    lines = (_classify(line.strip()) for line in WRAPPER_MACRO.sub("", markup).split("\n"))
    blocks: list[Block] = []
    for kind, group in groupby(lines, key=lambda line: line[0]):
        blocks += _group(kind, [(marks, words) for _, marks, words in group])
    return blocks


def _classify(line: str) -> tuple[str, str, str]:
    """What a line is, its marks (for a list item) and its words."""
    if not line:
        return "blank", "", ""
    if found := HEADING.fullmatch(line):
        return "heading", "", found.group(1)
    if RULE.fullmatch(line):
        return "rule", "", ""
    if found := QUOTE_LINE.fullmatch(line):
        return "quote", "", found.group(1)
    if found := LIST_ITEM.fullmatch(line):
        return "list", found.group(1), found.group(2)
    if line.startswith("|"):
        return "row", "", line
    return "text", "", line


def _group(kind: str, lines: list[tuple[str, str]]) -> list[Block]:
    """Lines of one kind in a row: one block, or one per line for headings and rules."""
    if kind == "heading":
        return [Heading(_inline(words)) for _, words in lines]
    if kind == "rule":
        return [Rule() for _ in lines]
    if kind == "list":
        return list(_lists(_list_entries(lines), 1))
    if kind == "row":
        return [Table(tuple(_row(words) for _, words in lines))]
    # A line holding only a picture shows nothing once the picture is beside the text.
    shown = tuple(
        line
        for line in (_paragraph_line(words) for _, words in lines)
        if isinstance(line, Point) or not _blank(line)
    )
    if not shown:
        return []
    if kind == "quote":
        return [Quote((Paragraph(shown),))]
    return [Paragraph(shown)]


def _paragraph_line(words: str) -> Line | Point:
    if found := POINT.fullmatch(words):
        return Point(found.group(1), _inline(found.group(2)))
    return _inline(words)


def _blank(pieces: Line) -> bool:
    return all(isinstance(piece, Text) and not piece.text.strip() for piece in pieces)


def _list_entries(lines: list[tuple[str, str]]) -> list[tuple[int, bool, Line]]:
    """Each item's depth, never more than one below the item before it, as Jira reads them;
    its last mark says numbered or bulleted."""
    entries = []
    depth = 0
    for marks, words in lines:
        depth = min(len(marks), depth + 1, MAX_LIST_DEPTH)
        entries.append((depth, marks.endswith("#"), _inline(words)))
    return entries


def _lists(entries: list[tuple[int, bool, Line]], depth: int) -> tuple[ListBlock, ...]:
    """Items at this depth with the deeper items after each as its own lists. A switch between
    bullets and numbers starts a new list."""
    lists: list[tuple[bool, list[ListItem]]] = []
    index = 0
    while index < len(entries):
        _, ordered, line = entries[index]
        end = index + 1
        while end < len(entries) and entries[end][0] > depth:
            end += 1
        item = ListItem(line, _lists(entries[index + 1 : end], depth + 1))
        if lists and lists[-1][0] == ordered:
            lists[-1][1].append(item)
        else:
            lists.append((ordered, [item]))
        index = end
    return tuple(ListBlock(ordered, tuple(items)) for ordered, items in lists)


def _row(line: str) -> tuple[Cell, ...]:
    """|cell|cell| and ||heading||heading||: a cell after "||" is a heading."""
    borders = _borders(line)
    cells = []
    for (start, after), (end, _) in pairwise([*borders, (len(line), len(line))]):
        words = line[after:end].strip()
        if end == len(line) and not words:
            continue
        cells.append(Cell(after - start == 2, _inline(words)))
    return tuple(cells)


def _borders(line: str) -> list[tuple[int, int]]:
    """Where each cell border starts and ends. A bar inside [words|address] or {{code}}, or
    after "\\", is not a border. A bracket that never closes is skipped at once, so a row of
    them is still read in one pass."""
    closers = {"[": "]", "{{": "}}"}
    last = {opener: line.rfind(closer) for opener, closer in closers.items()}
    borders = []
    index = 0
    while index < len(line):
        opener = "{{" if line.startswith("{{", index) else line[index]
        if opener == "\\":
            index += 2
        elif opener in closers and index < last[opener]:
            closer = closers[opener]
            index = line.find(closer, index + len(opener)) + len(closer)
        elif opener == "|":
            width = 2 if line.startswith("||", index) else 1
            borders.append((index, index + width))
            index += width
        else:
            index += 1
    return borders


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
    if kind == "picture":
        return None, match.end()
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
    try:
        parts = urlsplit(url)
    except ValueError:
        # "https://[broken": Python cannot read it, so it is not an address to open.
        return False
    if parts.scheme.lower() not in LINK_SCHEMES:
        return False
    jira_host = urlsplit(jira_address).hostname
    return parts.hostname is None or parts.hostname != jira_host


def _block_html(block: Block, jira_address: str) -> str:
    match block:
        case Paragraph(lines):
            return _paragraph_html(lines, jira_address)
        case Heading(line):
            return f'<p class="message-heading">{_html(line, jira_address)}</p>'
        case ListBlock(ordered, items):
            tag = "ol" if ordered else "ul"
            shown = "".join(_item_html(item, jira_address) for item in items)
            return f"<{tag}>{shown}</{tag}>"
        case Table(rows):
            shown = "".join(
                "<tr>" + "".join(_cell_html(cell, jira_address) for cell in row) + "</tr>"
                for row in rows
            )
            # Focusable, so a wide table on a phone scrolls from the keyboard too.
            return f'<div class="message-table" tabindex="0"><table>{shown}</table></div>'
        case CodeBlock(text):
            # Focusable like a table: a long line scrolls from the keyboard too.
            return f'<pre tabindex="0"><code>{escape(text)}</code></pre>'
        case Quote(blocks):
            inside = "".join(_block_html(inner, jira_address) for inner in blocks)
            return f"<blockquote>{inside}</blockquote>"
        case Rule():
            return "<hr>"


def _paragraph_html(lines: tuple[Line | Point, ...], jira_address: str) -> str:
    """Lines in a row go into one <p>; a point is a <p> of its own, so it can be laid out."""
    shown = ""
    plain: list[Line] = []
    for line in lines:
        if isinstance(line, Point):
            shown += _lines_html(plain, jira_address) + _point_html(line, jira_address)
            plain = []
        else:
            plain.append(line)
    return shown + _lines_html(plain, jira_address)


def _lines_html(lines: list[Line], jira_address: str) -> str:
    if not lines:
        return ""
    return "<p>" + "<br>".join(_html(line, jira_address) for line in lines) + "</p>"


def _point_html(point: Point, jira_address: str) -> str:
    # The space keeps "4. Request ID" when the point is copied; the grid ignores it.
    return (
        f'<p class="message-point"><strong>{point.number}.</strong> '
        f"<span>{_html(point.line, jira_address)}</span></p>"
    )


def _item_html(item: ListItem, jira_address: str) -> str:
    nested = "".join(_block_html(inner, jira_address) for inner in item.lists)
    return f"<li>{_html(item.line, jira_address)}{nested}</li>"


def _cell_html(cell: Cell, jira_address: str) -> str:
    tag = "th" if cell.header else "td"
    return f"<{tag}>{_html(cell.line, jira_address)}</{tag}>"


def _block_text(block: Block, jira_address: str, indent: str = "") -> str:
    match block:
        case Paragraph(lines):
            return "\n".join(_line_text(line, jira_address) for line in lines)
        case Heading(line):
            return _text(line, jira_address)
        case ListBlock(ordered, items):
            return "\n".join(
                _item_text(f"{number}." if ordered else "•", item, jira_address, indent)
                for number, item in enumerate(items, 1)
            )
        case Table(rows):
            return "\n".join(
                " | ".join(_text(cell.line, jira_address) for cell in row) for row in rows
            )
        case CodeBlock(text):
            return text
        case Quote(blocks):
            return "\n\n".join(_block_text(inner, jira_address) for inner in blocks)
        case Rule():
            return ""


def _line_text(line: Line | Point, jira_address: str) -> str:
    if isinstance(line, Point):
        return f"{line.number}. {_text(line.line, jira_address)}"
    return _text(line, jira_address)


def _item_text(mark: str, item: ListItem, jira_address: str, indent: str) -> str:
    lines = [f"{indent}{mark} {_text(item.line, jira_address)}"]
    lines += [_block_text(inner, jira_address, indent + "  ") for inner in item.lists]
    return "\n".join(lines)


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
