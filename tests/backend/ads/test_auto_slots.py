"""Automatic ad slots: spacing rules, paragraph detection and the HTML safety contract."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from apps.ads.services import auto_slots
from apps.ads.services.auto_slots import (
    AutoRule,
    inject_slots,
    rule_for,
    scan_blocks,
    tile_slots,
)

MARK = '<aside data-slot="ad">AD</aside>'
RULE = auto_slots.DEFAULT_RULES[auto_slots.IN_ARTICLE]
WORDS = "word " * 80


def paragraphs(count: int, *, words: str = WORDS) -> str:
    return "".join(f"<p>Paragraph {i}. {words}</p>\n" for i in range(1, count + 1))


class Counter:
    def __init__(self, limit: int | None = None) -> None:
        self.calls = 0
        self.limit = limit

    def __call__(self) -> str:
        self.calls += 1
        if self.limit is not None and self.calls > self.limit:
            return ""
        return MARK


def top_level(html: str) -> list[str]:
    blocks = scan_blocks(html)
    assert blocks is not None, "markup must stay well formed"
    return [block.tag for block in blocks]


class _Depth(HTMLParser):
    """Independent check: every ``aside`` is a direct child of the root."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.aside_depths: list[int] = []
        self.errors = 0

    def handle_starttag(self, tag, attrs):
        if tag == "aside":
            self.aside_depths.append(self.depth)
        if tag not in auto_slots.VOID_TAGS:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag not in auto_slots.VOID_TAGS:
            self.depth -= 1
            if self.depth < 0:
                self.errors += 1


def aside_depths(html: str) -> list[int]:
    parser = _Depth()
    parser.feed(html)
    assert parser.errors == 0 and parser.depth == 0
    return parser.aside_depths


# ------------------------------------------------------------------- tiles ----


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (0, []),
        (7, []),
        (8, []),  # the ad would be the last tile
        (9, [8]),
        (16, [8]),
        (17, [8, 16]),
        (60, [8, 16]),  # at most two by default
    ],
)
def test_tile_slots_are_count_aware_and_never_the_last_tile(total, expected):
    assert (
        tile_slots(total, auto_slots.DEFAULT_RULES[auto_slots.BETWEEN_ITEMS])
        == expected
    )


def test_tile_slots_always_leave_a_real_tile_after_the_ad():
    rule = AutoRule(first_after=3, every=3, max_slots=9, min_items=1)

    for total in range(40):
        slots = tile_slots(total, rule)
        assert all(position < total for position in slots)
        assert slots == sorted(set(slots))


def test_tile_slots_respect_the_page_budget():
    rule = auto_slots.DEFAULT_RULES[auto_slots.BETWEEN_ITEMS]

    assert tile_slots(40, rule, cap=1) == [8]
    assert tile_slots(40, rule, cap=0) == []
    assert tile_slots(40, rule, cap=-3) == []


def test_rule_for_uses_defaults_and_clamps_unsafe_placement_values():
    class Placement:
        auto_first_after = 0
        auto_every = 0
        auto_max = 0
        auto_min_items = 0

    placement = Placement()
    assert rule_for(None, auto_slots.IN_ARTICLE) == RULE
    assert rule_for(placement, auto_slots.IN_ARTICLE) == RULE
    placement.auto_first_after = 1  # would put an ad in the first paragraph
    placement.auto_every = 1  # would put ads back to back
    placement.auto_max = 7
    placement.auto_min_items = 3
    assert rule_for(placement, auto_slots.IN_ARTICLE) == AutoRule(2, 2, 7, 3)


# ------------------------------------------------------------ block scanner ----


def test_scan_blocks_reports_top_level_elements_only():
    html = (
        "<h2>T</h2><p>a</p><ul><li><p>inner</p></li></ul>"
        "<blockquote><p>q</p></blockquote><hr><p>b</p>"
    )

    assert top_level(html) == ["h2", "p", "ul", "blockquote", "hr", "p"]


def test_scan_blocks_offsets_slice_exactly_the_source():
    html = '<p title="a>b">one</p>\n<p>two</p>'
    first, second = scan_blocks(html)

    assert html[first.start : first.end] == '<p title="a>b">one</p>'
    assert html[second.start : second.end] == "<p>two</p>"


def test_scan_blocks_handles_crlf_line_endings():
    html = "<p>one</p>\r\n<p>two\r\nlines</p>\r\n<p>three</p>"
    blocks = scan_blocks(html)

    assert [html[b.start : b.end] for b in blocks] == [
        "<p>one</p>",
        "<p>two\r\nlines</p>",
        "<p>three</p>",
    ]


@pytest.mark.parametrize(
    "html",
    [
        "<p>unclosed",
        "<p>a<p>b</p>",
        "<div><p>a</div></p>",
        "</p><p>stray</p>",
    ],
)
def test_malformed_markup_is_refused(html):
    assert scan_blocks(html) is None


def test_scripts_and_comments_do_not_create_paragraphs():
    html = (
        "<p>real</p><script>document.write('</p><p>fake</p>')</script>"
        "<!-- <p>comment</p> --><style>p{}</style><p>real two</p>"
    )
    blocks = scan_blocks(html)

    assert [b.tag for b in blocks] == ["p", "script", "style", "p"]
    assert auto_slots.paragraph_count(blocks) == 2


def test_empty_and_image_only_paragraphs_are_not_text_paragraphs():
    html = '<p>&nbsp;</p><p><img src="x.png" alt=""></p><p></p><p>text</p>'

    assert auto_slots.paragraph_count(scan_blocks(html)) == 1


# --------------------------------------------------------------- insertion ----


def test_default_rule_puts_slots_after_paragraph_three_and_seven():
    source = paragraphs(10)

    result = inject_slots(source, RULE, Counter())

    parts = result.split(MARK)
    assert len(parts) == 3
    assert parts[0].count("<p>") == 3
    assert parts[1].count("<p>") == 4
    assert parts[2].count("<p>") == 3
    assert aside_depths(result) == [0, 0]


def test_the_original_markup_is_preserved_byte_for_byte():
    source = paragraphs(12)

    assert inject_slots(source, RULE, Counter()).replace(MARK, "") == source


def test_a_short_article_gets_no_ads():
    assert inject_slots(paragraphs(4), RULE, Counter()) == paragraphs(4)
    assert inject_slots(paragraphs(5), RULE, Counter()).count(MARK) == 1


def test_never_in_the_first_paragraph_and_never_two_in_a_row():
    for first_after in range(1, 8):
        rule = AutoRule(
            first_after=max(first_after, 2), every=2, max_slots=9, min_items=1
        )
        result = inject_slots(paragraphs(14), rule, Counter())

        assert not result.split(MARK)[0].count("<p>") < 2
        for chunk in result.split(MARK)[1:-1]:
            assert chunk.count("<p>") >= 2


def test_never_next_to_a_heading_a_list_a_table_a_figure_a_quote_or_code():
    blocks = {
        "heading": "<h2>Section</h2>",
        "list": "<ul><li>one</li><li>two</li></ul>",
        "ordered": "<ol><li>one</li></ol>",
        "table": "<table><tr><td>x</td></tr></table>",
        "figure": '<figure><img src="x.png" alt=""><figcaption>c</figcaption></figure>',
        "quote": "<blockquote><p>q</p></blockquote>",
        "code": "<pre><code>p = 1</code></pre>",
    }
    rule = AutoRule(first_after=2, every=2, max_slots=9, min_items=1)
    for name, block in blocks.items():
        source = paragraphs(2) + block + paragraphs(2) + block + paragraphs(3)

        result = inject_slots(source, rule, Counter())

        assert result.replace(MARK, "") == source, name
        assert aside_depths(result) == [0] * result.count(MARK), name
        for chunk_end, chunk_start in zip(
            result.split(MARK)[:-1], result.split(MARK)[1:], strict=True
        ):
            assert chunk_end.rstrip().endswith("</p>"), name
            assert chunk_start.lstrip().startswith("<p>"), name


def test_a_candidate_before_a_heading_moves_to_the_next_paragraph():
    source = paragraphs(3) + "<h2>Next</h2>" + paragraphs(4)

    result = inject_slots(source, RULE, Counter())

    before, after = result.split(MARK)
    assert before.count("<p>") == 4  # the heading blocks p3, so the slot follows p4
    assert before.rstrip().endswith("</p>") and after.lstrip().startswith("<p>")
    assert "<h2>Next</h2>" in before


def test_text_between_paragraphs_is_not_split():
    source = paragraphs(3).rstrip("\n") + "loose text" + paragraphs(4)

    assert MARK not in inject_slots(source, RULE, Counter()).split("loose text")[0]


def test_the_word_gap_between_ads_is_honoured():
    source = paragraphs(14, words="short ")
    rule = AutoRule(first_after=2, every=2, max_slots=9, min_items=1)

    dense = inject_slots(source, rule, Counter()).count(MARK)
    sparse = inject_slots(source, rule, Counter(), min_words_between=40).count(MARK)

    assert sparse < dense
    assert sparse >= 1


def test_the_cap_and_rule_maximum_limit_the_slots():
    rule = AutoRule(first_after=2, every=2, max_slots=2, min_items=1)

    assert inject_slots(paragraphs(20), rule, Counter()).count(MARK) == 2
    assert inject_slots(paragraphs(20), rule, Counter(), cap=1).count(MARK) == 1
    assert inject_slots(paragraphs(20), rule, Counter(), cap=0).count(MARK) == 0


def test_an_empty_answer_stops_the_insertion():
    rule = AutoRule(first_after=2, every=2, max_slots=9, min_items=1)
    counter = Counter(limit=1)

    result = inject_slots(paragraphs(20), rule, counter)

    assert result.count(MARK) == 1
    assert counter.calls == 2


def test_malformed_article_html_is_returned_untouched():
    broken = paragraphs(8) + "<p>never closed"

    assert inject_slots(broken, RULE, Counter()) == broken


def test_empty_input_and_input_without_paragraphs_are_untouched():
    for source in ("", "   ", "<h2>Only a heading</h2>", "<ul><li>a</li></ul>"):
        assert inject_slots(source, RULE, Counter()) == source


def test_tricky_nested_article_stays_valid():
    nested = (
        paragraphs(3)
        + "<div class='article-table'><table><tr><td><p>cell</p><p>cell 2</p></td></tr></table></div>"
        + '<figure><img src="a.png" alt=""><figcaption><p>cap</p></figcaption></figure>'
        + "<ul><li><p>one</p></li><li><p>two</p><ul><li><p>deep</p></li></ul></li></ul>"
        + "<script>var s = '</p><p>';</script>"
        + paragraphs(6)
    )

    result = inject_slots(nested, RULE, Counter())

    assert result.replace(MARK, "") == nested
    assert aside_depths(result) == [0] * result.count(MARK)
    assert re.findall(r"<aside", result).__len__() == result.count(MARK)
