"""Unit tests for the extraction skill library (F006).

Every skill is tested against Element fixtures that mirror real normalizer output.
"""
from __future__ import annotations

import datetime

import pytest

from ake.compiler.skills import (
    SKILL_REGISTRY,
    NamedEntity,
    extract_named_entities,
    extract_table,
    find_section,
    locate_by_proximity,
    normalize_currency,
    normalize_date,
    resolve_entity,
)
from ake.ingestion.element import Element


# ── Helper factories ────────────────────────────────────────────────────────


def _el(
    element_id: str = "e1",
    text: str = "",
    section_path: list[str] | None = None,
    el_type: str = "paragraph",  # type: ignore[assignment] — literal passed by callers
) -> Element:
    return Element(
        doc_id="doc1",
        element_id=element_id,
        type=el_type,
        text=text,
        page=1,
        section_path=section_path or [],
    )


def _table_el(element_id: str = "t1", text: str = "") -> Element:
    return _el(element_id=element_id, text=text, el_type="table")


def _heading_el(element_id: str = "h1", text: str = "") -> Element:
    return _el(element_id=element_id, text=text, el_type="title")


# ── Test data ───────────────────────────────────────────────────────────────

_SAMPLE_ELEMENTS: list[Element] = [
    _heading_el("h1", "Item 7 — Management Discussion"),
    _el("e1", "This section presents management's discussion.", section_path=["Item 7 — Management Discussion"]),
    _heading_el("h2", "Capital Returns"),
    _el("e2", "The company returned $2.4 billion to shareholders.", section_path=["Item 7 — Management Discussion", "Capital Returns"]),
    _heading_el("h3", "Share Repurchases"),
    _el("e3", "12 million shares bought back at $85.50.", section_path=["Item 7 — Management Discussion", "Capital Returns", "Share Repurchases"]),
    _heading_el("h4", "Dividends"),
    _el("e4", "Quarterly dividend increased from $0.25 to $0.30.", section_path=["Item 7 — Management Discussion", "Capital Returns", "Dividends"]),
    _el("e5", "Operating cash flow was $3.1 billion.", section_path=["Item 7 — Management Discussion"]),
]


# ═════════════════════════════════════════════════════════════════════════════
# SKILL_REGISTRY
# ═════════════════════════════════════════════════════════════════════════════


class TestSkillRegistry:
    """All seven core skills must appear in the registry with typed signatures."""

    def test_all_skills_registered(self):
        expected = {
            "extract_table",
            "find_section",
            "extract_named_entities",
            "normalize_currency",
            "normalize_date",
            "locate_by_proximity",
            "resolve_entity",
        }
        assert set(SKILL_REGISTRY.keys()) == expected

    def test_each_registry_entry_is_non_empty_str(self):
        for name, sig in SKILL_REGISTRY.items():
            assert isinstance(sig, str), f"{name} signature is not a string"
            assert len(sig) > 20, f"{name} signature too short"


# ═════════════════════════════════════════════════════════════════════════════
# extract_table
# ═════════════════════════════════════════════════════════════════════════════


class TestExtractTable:
    def test_finds_table_after_heading_fuzzy_match(self):
        elements = [
            _heading_el("h1", "Financial Data"),
            _el("e1", "Some paragraph."),
            _table_el("t1", "Year | Revenue | Profit\n2023 | 100 | 10\n2024 | 120 | 15"),
        ]
        rows = extract_table(elements, "Financial")
        assert len(rows) == 2
        assert rows[0] == {"Year": "2023", "Revenue": "100", "Profit": "10"}
        assert rows[1] == {"Year": "2024", "Revenue": "120", "Profit": "15"}

    def test_fuzzy_disabled_requires_exact_match(self):
        elements = [
            _heading_el("h1", "Financial Data"),
            _table_el("t1", "A | B\n1 | 2"),
        ]
        rows = extract_table(elements, "Financial", fuzzy=False)
        assert rows == []

        rows = extract_table(elements, "Financial Data", fuzzy=False)
        assert len(rows) == 1

    def test_case_sensitive_match(self):
        elements = [
            _heading_el("h1", "FINANCIAL DATA"),
            _table_el("t1", "A | B\n1 | 2"),
        ]
        rows = extract_table(elements, "financial data", case_sensitive=True)
        assert rows == []

        rows = extract_table(elements, "FINANCIAL DATA", case_sensitive=True)
        assert len(rows) == 1

    def test_heading_not_found_returns_empty(self):
        elements = [
            _heading_el("h1", "Introduction"),
            _table_el("t1", "A | B\n1 | 2"),
        ]
        rows = extract_table(elements, "Financial")
        assert rows == []

    def test_no_table_after_heading_returns_empty(self):
        elements = [
            _heading_el("h1", "Financial Data"),
            _el("e1", "Text only, no table."),
        ]
        rows = extract_table(elements, "Financial")
        assert rows == []

    def test_tab_separated_table(self):
        elements = [
            _heading_el("h1", "Tab Data"),
            _table_el("t1", "Col1\tCol2\tCol3\nA\t1\tX\nB\t2\tY"),
        ]
        rows = extract_table(elements, "Tab")
        assert len(rows) == 2
        assert rows[0] == {"Col1": "A", "Col2": "1", "Col3": "X"}

    def test_single_line_table_returns_empty(self):
        """Tables with only a header line and no data rows return empty."""
        elements = [
            _heading_el("h1", "Single"),
            _table_el("t1", "OnlyOneLine"),
        ]
        rows = extract_table(elements, "Single")
        assert rows == []

    def test_heading_as_header_type(self):
        """<h2> elements map to 'header' type — should still match."""
        elements = [
            _el("h1", "Capital Returns", el_type="header"),
            _table_el("t1", "A | B\n1 | 2"),
        ]
        rows = extract_table(elements, "Capital")
        assert len(rows) == 1


# ═════════════════════════════════════════════════════════════════════════════
# find_section
# ═════════════════════════════════════════════════════════════════════════════


class TestFindSection:
    def test_exact_path_match(self):
        result = find_section(
            _SAMPLE_ELEMENTS,
            ["Item 7 — Management Discussion", "Capital Returns", "Share Repurchases"],
        )
        assert len(result) == 1
        assert result[0].text == "12 million shares bought back at $85.50."

    def test_prefix_match(self):
        result = find_section(
            _SAMPLE_ELEMENTS,
            ["Item 7 — Management Discussion", "Capital Returns"],
        )
        # Should include paragraph about returns + both sub-sections
        texts = {e.text for e in result}
        assert "The company returned $2.4 billion to shareholders." in texts
        assert "12 million shares bought back at $85.50." in texts
        assert "Quarterly dividend increased from $0.25 to $0.30." in texts

    def test_top_level_match(self):
        result = find_section(_SAMPLE_ELEMENTS, ["Item 7 — Management Discussion"])
        assert len(result) == 5  # all elements under Item 7 (including sub-sections)

    def test_empty_path_returns_all(self):
        result = find_section(_SAMPLE_ELEMENTS, [])
        assert len(result) == len(_SAMPLE_ELEMENTS)

    def test_no_match_returns_empty(self):
        result = find_section(_SAMPLE_ELEMENTS, ["Non-existent Section"])
        assert result == []

    def test_returns_empty_for_empty_elements(self):
        result = find_section([], ["Any"])
        assert result == []


# ═════════════════════════════════════════════════════════════════════════════
# extract_named_entities
# ═════════════════════════════════════════════════════════════════════════════


class TestExtractNamedEntities:
    def test_extract_currency(self):
        entities = extract_named_entities("Revenue was $3.1 billion.", ["CURRENCY"])
        assert len(entities) == 1
        assert entities[0].kind == "CURRENCY"
        assert "3.1 billion" in entities[0].value

    def test_extract_date_fiscal_year(self):
        entities = extract_named_entities("In FY2022 revenue grew.", ["DATE"])
        assert len(entities) == 1
        assert entities[0].kind == "DATE"
        assert "FY2022" in entities[0].value

    def test_extract_date_iso(self):
        entities = extract_named_entities("Effective 2024-03-15.", ["DATE"])
        assert len(entities) == 1
        assert "2024-03-15" in entities[0].value

    def test_extract_date_long_form(self):
        entities = extract_named_entities("Filed on 31 Dec 2023.", ["DATE"])
        assert len(entities) == 1
        assert "31 Dec 2023" in entities[0].value

    def test_extract_org(self):
        entities = extract_named_entities("Acme Corp. and Beta Holdings merged.", ["ORG"])
        assert len(entities) == 2
        assert entities[0].value == "Acme Corp"
        assert entities[1].value == "Beta Holdings"

    def test_extract_percent(self):
        entities = extract_named_entities("Growth was 15% year-over-year.", ["PERCENT"])
        assert len(entities) == 1
        assert entities[0].kind == "PERCENT"
        assert "15%" in entities[0].value

    def test_extract_person(self):
        entities = extract_named_entities("John Smith led the division.", ["PERSON"])
        assert len(entities) == 1
        assert entities[0].kind == "PERSON"

    def test_extract_all_types(self):
        text = "Acme Corp. reported $3.1 billion revenue in FY2022, up 15% per John Smith."
        entities = extract_named_entities(text)
        kinds = {e.kind for e in entities}
        assert kinds == {"ORG", "CURRENCY", "DATE", "PERCENT", "PERSON"}

    def test_empty_text(self):
        assert extract_named_entities("") == []

    def test_char_positions_are_valid(self):
        text = "Acme Corp. earned $1.2B."
        entities = extract_named_entities(text)
        for e in entities:
            assert 0 <= e.char_start < e.char_end <= len(text)
            assert text[e.char_start:e.char_end] == e.value


# ═════════════════════════════════════════════════════════════════════════════
# normalize_currency
# ═════════════════════════════════════════════════════════════════════════════


class TestNormalizeCurrency:
    def test_dollar_billion_short(self):
        assert normalize_currency("$1.2B") == pytest.approx(1200.0)

    def test_dollar_billion_long(self):
        assert normalize_currency("$2.4 billion") == pytest.approx(2400.0)

    def test_millions_with_comma(self):
        assert normalize_currency("1,200 million") == pytest.approx(1200.0)

    def test_usd_billion_bn(self):
        assert normalize_currency("USD 1.2bn") == pytest.approx(1200.0)

    def test_plain_dollar(self):
        result = normalize_currency("$0.85")
        assert result == pytest.approx(0.00000085)

    def test_euro_million(self):
        assert normalize_currency("€500M") == pytest.approx(500.0)

    def test_pound_million(self):
        assert normalize_currency("£750M") == pytest.approx(750.0)

    def test_no_scale_suffix(self):
        result = normalize_currency("$5000000")
        assert result == pytest.approx(5.0)  # 5M / 1M = 5

    def test_thousand(self):
        assert normalize_currency("$300K") == pytest.approx(0.3)

    def test_trillion(self):
        assert normalize_currency("$1T") == pytest.approx(1_000_000.0)

    def test_case_insensitive_scale(self):
        assert normalize_currency("$100M") == normalize_currency("$100m")
        assert normalize_currency("$100m") == pytest.approx(100.0)

    def test_unparseable_returns_none(self):
        assert normalize_currency("not a number") is None
        assert normalize_currency("") is None
        assert normalize_currency("just text") is None

    def test_currency_edge_cases(self):
        """All F006 edge cases in one place."""
        assert normalize_currency("$1.2B") == pytest.approx(1200.0)
        assert normalize_currency("1,200 million") == pytest.approx(1200.0)
        assert normalize_currency("USD 1.2bn") == pytest.approx(1200.0)

    def test_usd_thousand(self):
        assert normalize_currency("USD 450.5K") == pytest.approx(0.4505)

    def test_decimal_millions(self):
        assert normalize_currency("$0.5M") == pytest.approx(0.5)

    def test_integer_millions_direct(self):
        """$85.50 → should not match since no scale suffix — direct dollar."""
        result = normalize_currency("$85.50")
        assert result == pytest.approx(0.0000855)


# ═════════════════════════════════════════════════════════════════════════════
# normalize_date
# ═════════════════════════════════════════════════════════════════════════════


class TestNormalizeDate:
    def test_fiscal_year_four_digit(self):
        assert normalize_date("FY2022") == datetime.date(2022, 1, 1)

    def test_fiscal_year_two_digit(self):
        assert normalize_date("FY22") == datetime.date(2022, 1, 1)

    def test_fiscal_year_with_space(self):
        assert normalize_date("FY 2023") == datetime.date(2023, 1, 1)

    def test_quarter(self):
        assert normalize_date("Q3 2025") == datetime.date(2025, 7, 1)

    def test_quarter_q1(self):
        assert normalize_date("Q1 2024") == datetime.date(2024, 1, 1)

    def test_day_month_year(self):
        assert normalize_date("31 Dec 2023") == datetime.date(2023, 12, 31)

    def test_day_month_abbreviated(self):
        assert normalize_date("15 Jan 2026") == datetime.date(2026, 1, 15)

    def test_month_day_comma_year(self):
        assert normalize_date("Jan. 15, 2026") == datetime.date(2026, 1, 15)

    def test_month_year(self):
        assert normalize_date("December 2024") == datetime.date(2024, 12, 1)

    def test_iso_format(self):
        assert normalize_date("2024-03-15") == datetime.date(2024, 3, 15)

    def test_full_month_name_day_year(self):
        assert normalize_date("January 31, 2025") == datetime.date(2025, 1, 31)

    def test_case_insensitive(self):
        assert normalize_date("31 dec 2023") == datetime.date(2023, 12, 31)

    def test_unparseable_returns_none(self):
        assert normalize_date("not a date") is None
        assert normalize_date("") is None
        assert normalize_date("random text") is None


# ═════════════════════════════════════════════════════════════════════════════
# locate_by_proximity
# ═════════════════════════════════════════════════════════════════════════════


class TestLocateByProximity:
    _PROX_ELEMENTS: list[Element] = [
        _el("e1", "Preamble paragraph."),
        _el("e2", "The target sentence is here.", section_path=["Section A"]),
        _el("e3", "Immediate neighbour after target."),
        _el("e4", "Another nearby element."),
        _el("e5", "Far away from the target."),
        _el("e6", "Very far indeed."),
    ]

    def test_finds_anchor_and_neighbours(self):
        result = locate_by_proximity(self._PROX_ELEMENTS, "target sentence", window=2)
        texts = {e.text for e in result}
        # Position: e1=0, e2=1 (anchor), e3=2, e4=3, e5=4, e6=5
        # window=2 → [-1..3] i.e. indices 0,1,2,3
        assert "Preamble paragraph." in texts
        assert "The target sentence is here." in texts
        assert "Immediate neighbour after target." in texts
        assert "Another nearby element." in texts

    def test_window_of_one(self):
        result = locate_by_proximity(self._PROX_ELEMENTS, "target sentence", window=1)
        texts = {e.text for e in result}
        # window=1 → indices 0,1,2
        assert "Preamble paragraph." in texts
        assert "The target sentence is here." in texts
        assert "Immediate neighbour after target." in texts
        assert "Another nearby element." not in texts

    def test_case_sensitive(self):
        result = locate_by_proximity(
            self._PROX_ELEMENTS, "TARGET SENTENCE", window=1, case_sensitive=True,
        )
        assert len(result) == 0

    def test_anchor_not_found_returns_empty(self):
        result = locate_by_proximity(self._PROX_ELEMENTS, "nonexistent", window=3)
        assert result == []

    def test_de_duplicates_overlapping_windows(self):
        """Two close anchors should not produce duplicate elements."""
        elements = [
            _el("e1", "anchor one here"),
            _el("e2", "anchor two here"),
            _el("e3", "The anchor is 'one'."),
            _el("e4", "also has 'anchor'."),
            _el("e5", "far away."),
        ]
        result = locate_by_proximity(elements, "anchor", window=2)
        ids = [e.element_id for e in result]
        # All elements should be included (windows overlap), no duplicates
        assert len(ids) == len(set(ids))
        # e1 and e4 contain "anchor", e2 contains "anchor" too
        assert len(result) == 5  # all elements

    def test_bounds_clamped_at_edges(self):
        """Anchor at the start — window should not go negative."""
        elements = [
            _el("e1", "start anchor"),
            _el("e2", "neighbour"),
            _el("e3", "another"),
        ]
        result = locate_by_proximity(elements, "start anchor", window=5)
        assert len(result) == 3


# ═════════════════════════════════════════════════════════════════════════════
# resolve_entity
# ═════════════════════════════════════════════════════════════════════════════


class TestResolveEntity:
    _REGISTRY = {
        "NVIDIA Corporation": "NVDA",
        "Microsoft Corp.": "MSFT",
        "Walmart Inc.": "WMT",
        "Apple Inc.": "AAPL",
    }

    def test_exact_match(self):
        assert resolve_entity("NVIDIA Corporation", self._REGISTRY) == "NVDA"

    def test_case_insensitive_match(self):
        assert resolve_entity("nvidia corporation", self._REGISTRY) == "NVDA"

    def test_substring_entity_in_registry(self):
        """Registry key contains the search name as substring."""
        assert resolve_entity("NVIDIA", self._REGISTRY) == "NVDA"

    def test_substring_registry_in_entity(self):
        """Search name contains the registry key."""
        assert resolve_entity("Microsoft", self._REGISTRY) == "MSFT"

    def test_ambiguous_picks_longest_key(self):
        registry = {
            "Apple": "AAPL_SHORT",
            "Apple Inc.": "AAPL",
        }
        assert resolve_entity("Apple Inc.", registry) == "AAPL"

    def test_no_match_returns_none(self):
        assert resolve_entity("Unknown Entity", self._REGISTRY) is None

    def test_empty_name_returns_none(self):
        assert resolve_entity("", self._REGISTRY) is None

    def test_empty_registry_returns_none(self):
        assert resolve_entity("Anything", {}) is None

    def test_case_sensitive_mode(self):
        assert resolve_entity("nvidia corporation", self._REGISTRY, case_sensitive=True) is None
        assert resolve_entity("NVIDIA Corporation", self._REGISTRY, case_sensitive=True) == "NVDA"

    def test_whitespace_trimmed(self):
        assert resolve_entity("  NVIDIA  ", self._REGISTRY) == "NVDA"


# ═════════════════════════════════════════════════════════════════════════════
# Integration / composability smoke tests
# ═════════════════════════════════════════════════════════════════════════════


class TestIntegrationSmoke:
    """Demonstrate that skills compose — the compiler loop chains them."""

    def test_extract_entities_then_normalize(self):
        """Realistic pipeline: find currency entities then normalize them."""
        text = "Acme Corp. reported $3.1 billion in FY2022."
        entities = extract_named_entities(text, ["CURRENCY"])
        assert len(entities) == 1
        value_millions = normalize_currency(entities[0].value)
        assert value_millions == pytest.approx(3100.0)

    def test_find_section_then_extract_entities(self):
        """Find a section, then scan its text for entities."""
        section = find_section(
            _SAMPLE_ELEMENTS,
            ["Item 7 — Management Discussion", "Capital Returns", "Share Repurchases"],
        )
        assert len(section) == 1
        entities = extract_named_entities(section[0].text, ["CURRENCY"])
        assert len(entities) == 1
        assert "$85.50" in entities[0].value

    def test_locate_anchor_then_normalize_currency(self):
        """Find text near an anchor phrase, then normalize currency values."""
        elements = [
            _el("e1", "The share repurchase programme is detailed below."),
            _el("e2", "Total outlay was $1.2B across the fiscal year."),
            _el("e3", "The board approved additional $500M."),
        ]
        neighbours = locate_by_proximity(elements, "repurchase", window=2)
        texts = [e.text for e in neighbours]
        assert "Total outlay was $1.2B across the fiscal year." in texts

        # Find currency values in neighbours
        all_text = " ".join(e.text for e in neighbours)
        currency_entities = extract_named_entities(all_text, ["CURRENCY"])
        values = [normalize_currency(e.value) for e in currency_entities]
        assert any(v == pytest.approx(1200.0) for v in values)

    def test_skill_registry_is_callable(self):
        """Every key in SKILL_REGISTRY must be importable and callable."""
        from ake.compiler import skills as m

        for name in SKILL_REGISTRY:
            fn = getattr(m, name)
            assert callable(fn), f"{name} is not callable"