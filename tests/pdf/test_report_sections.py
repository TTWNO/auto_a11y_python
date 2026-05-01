"""Unit tests for :mod:`auto_a11y.pdf.audit.report_sections`.

The builder module turns an :class:`AuditContext` into the
``dict[str, object]`` that lands on
``AuditResult.report_sections`` and (after persistence) on
``TestResult.metadata['report_sections']``.

Tests target the per-section public shape — the exact keys and
nested structures that ``pdf/_report_sections.html`` reads. Changing
the shape MUST go hand-in-hand with the template; the tests here
catch silent contract drift.
"""
from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

from auto_a11y.pdf.audit.colors import ColorPairInfo
from auto_a11y.pdf.audit.fonts import FontAnalysis, FontInfo
from auto_a11y.pdf.audit.report_sections import build_report_sections
from auto_a11y.pdf.audit.structure import StructElement


def _make_element(
    *,
    index: int,
    tag: str = "P",
    parent_index: int = -1,
    children: list[int] | None = None,
    text: str = "",
    alt: str | None = None,
    actual: str | None = None,
    lang: str | None = None,
) -> StructElement:
    """Build a StructElement fixture with sensible defaults."""
    return StructElement(
        index=index,
        custom_tag=tag,
        resolved_tag=tag,
        alt_text=alt,
        actual_text=actual,
        lang=lang,
        children_indices=children or [],
        mcids=[],
        parent_index=parent_index,
        obj=MagicMock(),
        text_content=text,
    )


def _make_ctx(
    *,
    elements: list[StructElement] | None = None,
    images: object = None,
    color_pairs: object = None,
    font_analysis: FontAnalysis | None = None,
) -> MagicMock:
    """Build a minimal AuditContext-shaped object.

    The builder reads only ``elements``, ``images``, ``color_pairs``,
    ``font_analysis``, and ``pdf`` — we don't need a real
    AuditContext here. ``MagicMock`` lets tests freely reassign
    ``.pdf`` to a real ``pikepdf.Pdf`` for builders that need pikepdf
    walks.
    """
    ctx = MagicMock()
    ctx.elements = elements or []
    ctx.images = images or []
    ctx.color_pairs = color_pairs or {}
    ctx.font_analysis = font_analysis
    # Mock a pikepdf.Pdf with no /Lang in the catalog.
    ctx.pdf = MagicMock()
    ctx.pdf.Root = MagicMock()
    ctx.pdf.Root.get = MagicMock(return_value=None)
    return ctx


# ---------------------------------------------------------------------------
# Master shape
# ---------------------------------------------------------------------------


class TestBuilderMasterShape:

    def test_returns_all_phase_abc_section_keys(self) -> None:
        out = build_report_sections(_make_ctx())
        # All Phase A + B + C section keys must appear, even on empty input.
        assert isinstance(out, dict)
        assert set(out.keys()) >= {
            # Phase A
            "tag_tree",
            "reading_order",
            "image_inventory",
            "heading_map",
            "full_alt_text",
            "color_contrast",
            "language_analysis",
            "font_analysis",
            # Phase B
            "link_inventory",
            "form_inventory",
            "wcag_mapping",
            "version_recommendations",
            # Phase C
            "executive_summary",
            "visual_reading_order",
            "exported_images",
            "images_of_text",
        }

    def test_payload_is_json_serialisable(self) -> None:
        """Mongo persistence requires the payload to be JSON-friendly."""
        import json
        out = build_report_sections(_make_ctx()) 
        # `set` would break this; if the builder regresses to using a
        # set we want to know immediately.
        json.dumps(out, default=str)


# ---------------------------------------------------------------------------
# §2 Tag Tree
# ---------------------------------------------------------------------------


class TestTagTree:

    def test_walks_root_down_in_order(self) -> None:
        elements = [
            _make_element(index=0, tag="Document", children=[1, 2]),
            _make_element(index=1, tag="H1", parent_index=0, text="Title"),
            _make_element(index=2, tag="P", parent_index=0, text="Body"),
        ]
        out = build_report_sections(_make_ctx(elements=elements)) 
        tt = cast("dict[str, object]", out["tag_tree"])
        rows = cast("list[dict[str, object]]", tt["rows"])
        assert [r["tag"] for r in rows] == ["Document", "H1", "P"]
        assert [r["depth"] for r in rows] == [0, 1, 1]
        assert tt["total_elements"] == 3

    def test_handles_empty_elements(self) -> None:
        out = build_report_sections(_make_ctx(elements=[])) 
        tt = cast("dict[str, object]", out["tag_tree"])
        assert tt == {"rows": [], "total_elements": 0}


# ---------------------------------------------------------------------------
# §3 Reading Order
# ---------------------------------------------------------------------------


class TestReadingOrder:

    def test_skips_artifact_tags(self) -> None:
        elements = [
            _make_element(index=0, tag="P", text="Visible"),
            _make_element(index=1, tag="Artifact", text="Hidden"),
        ]
        out = build_report_sections(_make_ctx(elements=elements)) 
        ro = cast("dict[str, object]", out["reading_order"])
        items = cast("list[dict[str, object]]", ro["items"])
        assert [i["text_preview"] for i in items] == ["Visible"]

    def test_skips_empty_text(self) -> None:
        elements = [_make_element(index=0, tag="P", text="")]
        out = build_report_sections(_make_ctx(elements=elements)) 
        ro = cast("dict[str, object]", out["reading_order"])
        assert ro["items"] == []


# ---------------------------------------------------------------------------
# §4 Image Inventory + §8 Full Alt Text
# ---------------------------------------------------------------------------


class TestImageInventory:

    def test_collects_figures_and_marks_missing_alt(self) -> None:
        elements = [
            _make_element(index=0, tag="Figure", alt="Logo"),
            _make_element(index=1, tag="Figure"),  # No alt
            _make_element(index=2, tag="P", text="not an image"),
        ]
        out = build_report_sections(_make_ctx(elements=elements)) 
        ii = cast("dict[str, object]", out["image_inventory"])
        figs = cast("list[dict[str, object]]", ii["figures"])
        assert len(figs) == 2
        assert figs[0]["has_alt"] is True
        assert figs[1]["has_alt"] is False

    def test_full_alt_text_lists_every_figure(self) -> None:
        elements = [
            _make_element(index=0, tag="Figure", alt="A"),
            _make_element(index=1, tag="Formula", alt="x + y"),
        ]
        out = build_report_sections(_make_ctx(elements=elements)) 
        fa = cast("dict[str, object]", out["full_alt_text"])
        items = cast("list[dict[str, object]]", fa["items"])
        assert len(items) == 2


# ---------------------------------------------------------------------------
# §7 Heading Map
# ---------------------------------------------------------------------------


class TestHeadingMap:

    def test_detects_level_skip(self) -> None:
        elements = [
            _make_element(index=0, tag="H1", text="Top"),
            _make_element(index=1, tag="H3", text="Skip"),  # Skipped H2
        ]
        out = build_report_sections(_make_ctx(elements=elements)) 
        hm = cast("dict[str, object]", out["heading_map"])
        gaps = cast("list[dict[str, object]]", hm["gaps"])
        assert len(gaps) == 1
        assert gaps[0]["from_level"] == 1
        assert gaps[0]["to_level"] == 3

    def test_no_gap_for_proper_hierarchy(self) -> None:
        elements = [
            _make_element(index=0, tag="H1", text="A"),
            _make_element(index=1, tag="H2", text="B"),
        ]
        out = build_report_sections(_make_ctx(elements=elements)) 
        hm = cast("dict[str, object]", out["heading_map"])
        assert hm["gaps"] == []


# ---------------------------------------------------------------------------
# §10 Color Contrast
# ---------------------------------------------------------------------------


class TestColorContrast:

    def test_computes_contrast_ratio_and_aa_flag(self) -> None:
        # Black on white = 21:1 (max contrast).
        info = ColorPairInfo()
        info.count = 100
        info.size_min = 12.0
        info.size_max = 12.0
        info.sample = "Hello"
        info.pages = {0}
        pairs: dict[tuple[tuple[float, float, float],
                          tuple[float, float, float]], ColorPairInfo] = {
            ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)): info,
        }
        out = build_report_sections(_make_ctx(color_pairs=pairs)) 
        cc = cast("dict[str, object]", out["color_contrast"])
        rows = cast("list[dict[str, object]]", cc["rows"])
        assert len(rows) == 1
        ratio = cast(float, rows[0]["contrast_ratio"])
        assert ratio > 20.0
        assert rows[0]["passes_aa_normal"] is True
        assert rows[0]["fg_hex"] == "#000000"
        assert rows[0]["bg_hex"] == "#ffffff"

    def test_low_contrast_fails_aa(self) -> None:
        # Light grey on white — well below 4.5:1.
        info = ColorPairInfo()
        info.count = 50
        info.size_min = 10.0
        info.size_max = 10.0
        info.sample = ""
        info.pages = set()
        pairs = {
            ((0.85, 0.85, 0.85), (1.0, 1.0, 1.0)): info,
        }
        out = build_report_sections(_make_ctx(color_pairs=pairs)) 
        cc = cast("dict[str, object]", out["color_contrast"])
        rows = cast("list[dict[str, object]]", cc["rows"])
        assert rows[0]["passes_aa_normal"] is False


# ---------------------------------------------------------------------------
# §11 Language of Parts
# ---------------------------------------------------------------------------


class TestLanguageAnalysis:

    def test_lists_only_spans_differing_from_doc_lang(self) -> None:
        # Build a minimal in-memory pikepdf.Pdf with /Lang set so the
        # builder's pikepdf.Dictionary narrowing path takes effect.
        import pikepdf
        pdf = pikepdf.Pdf.new()
        pdf.Root["/Lang"] = pikepdf.String("en")

        elements = [
            _make_element(index=0, tag="P", lang="fr-CA", text="Bonjour"),
            _make_element(index=1, tag="P", lang="en", text="Hello"),
            _make_element(index=2, tag="P", lang=None, text="Untagged"),
        ]
        ctx = _make_ctx(elements=elements)
        ctx.pdf = pdf
        out = build_report_sections(ctx) 
        la = cast("dict[str, object]", out["language_analysis"])
        spans = cast("list[dict[str, object]]", la["spans"])
        assert la["declared_lang"] == "en"
        # Only the fr-CA element shows up; en matches doc-lang, untagged
        # has no /Lang and is implicit doc-lang.
        assert len(spans) == 1
        assert spans[0]["lang"] == "fr-CA"


# ---------------------------------------------------------------------------
# §13 Font Analysis (full inventory)
# ---------------------------------------------------------------------------


class TestFontAnalysis:

    def test_inventory_sorted_by_char_count_descending(self) -> None:
        large = FontInfo(name="LargeFont")
        large.char_count = 1000
        large.sizes = {12.0}
        large.pages = {0}
        small = FontInfo(name="SmallFont")
        small.char_count = 50
        small.sizes = {10.0}
        small.pages = {0}
        fa = FontAnalysis(
            fonts={"LargeFont": large, "SmallFont": small},
            rotations=[],
            italic_runs=[],
            line_spacings=[],
            alignments=[],
        )
        out = build_report_sections(_make_ctx(font_analysis=fa)) 
        font = cast("dict[str, object]", out["font_analysis"])
        inv = cast("list[dict[str, object]]", font["inventory"])
        assert [row["name"] for row in inv] == ["LargeFont", "SmallFont"]
        # Percentages sum to ~100 (within rounding).
        total_pct = sum(cast(float, row["char_pct"]) for row in inv)
        assert 99.0 <= total_pct <= 101.0

    def test_empty_font_analysis_yields_empty_lists(self) -> None:
        out = build_report_sections(_make_ctx(font_analysis=None)) 
        font = cast("dict[str, object]", out["font_analysis"])
        assert font["inventory"] == []
        assert font["rotations"] == []
        assert font["total_chars"] == 0


# ---------------------------------------------------------------------------
# §5 Link Inventory (Phase B)
# ---------------------------------------------------------------------------


class TestLinkInventory:

    def test_lists_tagged_link_elements(self) -> None:
        elements = [
            _make_element(index=0, tag="Link", text="Click here"),
            _make_element(index=1, tag="P", text="not a link"),
        ]
        out = build_report_sections(_make_ctx(elements=elements)) 
        li = cast("dict[str, object]", out["link_inventory"])
        tagged = cast("list[dict[str, object]]", li["tagged_links"])
        assert len(tagged) == 1
        assert tagged[0]["text_preview"] == "Click here"

    def test_walks_link_annotations_from_pages(self) -> None:
        import pikepdf
        pdf = pikepdf.Pdf.new()
        # Empty pdf has zero pages → no annotation links. We only
        # need to verify the builder doesn't crash on empty input.
        ctx = _make_ctx()
        ctx.pdf = pdf
        out = build_report_sections(ctx)
        li = cast("dict[str, object]", out["link_inventory"])
        assert li["annotation_links"] == []
        assert li["tagged_links"] == []

    def test_uri_annotation_target_resolves_to_url(self) -> None:
        import pikepdf
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page(page_size=(612, 792))
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            A=pikepdf.Dictionary(
                Type=pikepdf.Name("/Action"),
                S=pikepdf.Name("/URI"),
                URI=pikepdf.String("https://example.com"),
            ),
        )
        page.Annots = pikepdf.Array([annot])
        ctx = _make_ctx()
        ctx.pdf = pdf
        out = build_report_sections(ctx)
        li = cast("dict[str, object]", out["link_inventory"])
        ann = cast("list[dict[str, object]]", li["annotation_links"])
        assert len(ann) == 1
        assert ann[0]["uri"] == "https://example.com"

    def test_internal_goto_array_dest_resolves_to_page_number(self) -> None:
        """A TOC-style /A /GoTo with explicit array dest reports 'page N'."""
        import pikepdf
        pdf = pikepdf.Pdf.new()
        page1 = pdf.add_blank_page(page_size=(612, 792))
        page3 = pdf.add_blank_page(page_size=(612, 792))
        pdf.add_blank_page(page_size=(612, 792))
        # The annotation lives on page 1 and points to page 3 (index 1).
        # Need the destination array to reference page3 by indirect ref;
        # pikepdf only emits indirect refs when the target is in the
        # object table, which it is once added to .pages.
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            A=pikepdf.Dictionary(
                Type=pikepdf.Name("/Action"),
                S=pikepdf.Name("/GoTo"),
                D=pikepdf.Array([page3.obj, pikepdf.Name("/Fit")]),
            ),
        )
        page1.Annots = pikepdf.Array([annot])
        ctx = _make_ctx()
        ctx.pdf = pdf
        out = build_report_sections(ctx)
        li = cast("dict[str, object]", out["link_inventory"])
        ann = cast("list[dict[str, object]]", li["annotation_links"])
        assert len(ann) == 1
        assert ann[0]["uri"] == "page 2"  # page3 is the second page (index 1) → "page 2"

    def test_direct_dest_on_annotation_is_resolved(self) -> None:
        """A legacy /Dest directly on the annotation is honoured."""
        import pikepdf
        pdf = pikepdf.Pdf.new()
        page1 = pdf.add_blank_page(page_size=(612, 792))
        page2 = pdf.add_blank_page(page_size=(612, 792))
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            Dest=pikepdf.Array([page2.obj, pikepdf.Name("/Fit")]),
        )
        page1.Annots = pikepdf.Array([annot])
        ctx = _make_ctx()
        ctx.pdf = pdf
        out = build_report_sections(ctx)
        li = cast("dict[str, object]", out["link_inventory"])
        ann = cast("list[dict[str, object]]", li["annotation_links"])
        assert len(ann) == 1
        assert ann[0]["uri"] == "page 2"

    def test_named_destination_resolves_via_names_dests_tree(self) -> None:
        """A named destination resolves through /Names /Dests."""
        import pikepdf
        pdf = pikepdf.Pdf.new()
        page1 = pdf.add_blank_page(page_size=(612, 792))
        page2 = pdf.add_blank_page(page_size=(612, 792))
        # Build a minimal name tree leaf with one (name, value) pair
        # where value is an explicit destination array pointing at page2.
        names_leaf = pikepdf.Dictionary(
            Names=pikepdf.Array([
                pikepdf.String("Section1"),
                pikepdf.Array([page2.obj, pikepdf.Name("/Fit")]),
            ]),
        )
        pdf.Root[pikepdf.Name("/Names")] = pikepdf.Dictionary(Dests=names_leaf)
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            A=pikepdf.Dictionary(
                Type=pikepdf.Name("/Action"),
                S=pikepdf.Name("/GoTo"),
                D=pikepdf.String("Section1"),
            ),
        )
        page1.Annots = pikepdf.Array([annot])
        ctx = _make_ctx()
        ctx.pdf = pdf
        out = build_report_sections(ctx)
        li = cast("dict[str, object]", out["link_inventory"])
        ann = cast("list[dict[str, object]]", li["annotation_links"])
        assert len(ann) == 1
        assert ann[0]["uri"] == "page 2 (named: Section1)"

    def test_unresolvable_named_destination_falls_back_to_label(self) -> None:
        """Without /Names tree, a named dest reports 'named: name'."""
        import pikepdf
        pdf = pikepdf.Pdf.new()
        page1 = pdf.add_blank_page(page_size=(612, 792))
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            A=pikepdf.Dictionary(
                Type=pikepdf.Name("/Action"),
                S=pikepdf.Name("/GoTo"),
                D=pikepdf.String("Missing"),
            ),
        )
        page1.Annots = pikepdf.Array([annot])
        ctx = _make_ctx()
        ctx.pdf = pdf
        out = build_report_sections(ctx)
        li = cast("dict[str, object]", out["link_inventory"])
        ann = cast("list[dict[str, object]]", li["annotation_links"])
        assert len(ann) == 1
        assert ann[0]["uri"] == "named: Missing"

    def test_gotor_action_renders_filespec_and_dest(self) -> None:
        """A /GoToR action renders 'file → dest' for cross-document links."""
        import pikepdf
        pdf = pikepdf.Pdf.new()
        page1 = pdf.add_blank_page(page_size=(612, 792))
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            A=pikepdf.Dictionary(
                Type=pikepdf.Name("/Action"),
                S=pikepdf.Name("/GoToR"),
                F=pikepdf.String("other.pdf"),
                D=pikepdf.String("Section2"),
            ),
        )
        page1.Annots = pikepdf.Array([annot])
        ctx = _make_ctx()
        ctx.pdf = pdf
        out = build_report_sections(ctx)
        li = cast("dict[str, object]", out["link_inventory"])
        ann = cast("list[dict[str, object]]", li["annotation_links"])
        assert ann[0]["uri"] == "other.pdf → named: Section2"

    def test_tagged_link_objr_walk_resolves_goto_destination(self) -> None:
        """A tagged Link struct element's /K → /OBJR → /A → /GoTo resolves."""
        import pikepdf
        pdf = pikepdf.Pdf.new()
        page1 = pdf.add_blank_page(page_size=(612, 792))
        page2 = pdf.add_blank_page(page_size=(612, 792))
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            A=pikepdf.Dictionary(
                Type=pikepdf.Name("/Action"),
                S=pikepdf.Name("/GoTo"),
                D=pikepdf.Array([page2.obj, pikepdf.Name("/Fit")]),
            ),
        )
        page1.Annots = pikepdf.Array([annot])
        # Build a Link struct element whose /K is an OBJR pointing at the annot.
        link_obj = pikepdf.Dictionary(
            S=pikepdf.Name("/Link"),
            K=pikepdf.Array([
                pikepdf.Dictionary(
                    Type=pikepdf.Name("/OBJR"),
                    Obj=annot,
                ),
            ]),
        )
        # The struct element fixture stores its dict as `obj` directly.
        elem = StructElement(
            index=0,
            custom_tag="/Link",
            resolved_tag="Link",
            alt_text=None,
            actual_text=None,
            lang=None,
            children_indices=[],
            mcids=[],
            parent_index=-1,
            obj=link_obj,
            text_content="See section 2",
        )
        ctx = _make_ctx(elements=[elem])
        ctx.pdf = pdf
        out = build_report_sections(ctx)
        li = cast("dict[str, object]", out["link_inventory"])
        tagged = cast("list[dict[str, object]]", li["tagged_links"])
        assert len(tagged) == 1
        assert tagged[0]["url"] == "page 2"
        assert tagged[0]["text_preview"] == "See section 2"


# ---------------------------------------------------------------------------
# §6 Form Field Inventory (Phase B)
# ---------------------------------------------------------------------------


class TestFormInventory:

    def test_no_acroform_yields_empty_summary(self) -> None:
        import pikepdf
        pdf = pikepdf.Pdf.new()  # No AcroForm.
        ctx = _make_ctx()
        ctx.pdf = pdf
        out = build_report_sections(ctx) 
        form = cast("dict[str, object]", out["form_inventory"])
        assert form["total"] == 0
        assert form["fields"] == []

    def test_decodes_required_and_readonly_flags(self) -> None:
        import pikepdf
        pdf = pikepdf.Pdf.new()
        # Build a single text field with /Ff = 3 (required + readonly).
        field = pdf.make_indirect(pikepdf.Dictionary({
            "/T": pikepdf.String("name"),
            "/FT": pikepdf.Name("/Tx"),
            "/TU": pikepdf.String("Your name"),
            "/Ff": 3,
            "/V": pikepdf.String("default"),
        }))
        pdf.Root["/AcroForm"] = pikepdf.Dictionary({
            "/Fields": pikepdf.Array([field]),
        })
        ctx = _make_ctx()
        ctx.pdf = pdf
        out = build_report_sections(ctx) 
        form = cast("dict[str, object]", out["form_inventory"])
        fields = cast("list[dict[str, object]]", form["fields"])
        assert len(fields) == 1
        f = fields[0]
        assert f["is_required"] is True
        assert f["is_read_only"] is True
        assert f["has_accessible_name"] is True
        assert f["type_label"] == "Text"


# ---------------------------------------------------------------------------
# §12 WCAG Mapping (Phase B)
# ---------------------------------------------------------------------------


class TestWcagMapping:

    def test_pass_verdict_when_all_related_checks_pass(self) -> None:
        from auto_a11y.pdf.models import CheckResult
        # 1.1.1's check name "Alt text on all Figure/Art tags" must
        # appear with PASS for the SC to roll up to PASS.
        crs = [
            CheckResult(
                name="Alt text on all Figure/Art tags",
                standard="WCAG 1.1.1",
                result="PASS",
                details="ok",
            ),
        ]
        out = build_report_sections(_make_ctx(), check_results=crs) 
        wm = cast("dict[str, object]", out["wcag_mapping"])
        rows = cast("list[dict[str, object]]", wm["rows"])
        sc_111 = next(r for r in rows if r["criterion"] == "1.1.1 Non-text Content")
        assert sc_111["verdict"] == "PASS"

    def test_fail_dominates_warn_dominates_pass(self) -> None:
        from auto_a11y.pdf.models import CheckResult
        crs = [
            CheckResult(
                name="Alt text on all Figure/Art tags",
                standard="WCAG 1.1.1",
                result="PASS",
                details="ok",
            ),
            CheckResult(
                name="Alt text adequacy",
                standard="WCAG 1.1.1",
                result="FAIL",
                details="bad",
            ),
        ]
        out = build_report_sections(_make_ctx(), check_results=crs) 
        wm = cast("dict[str, object]", out["wcag_mapping"])
        rows = cast("list[dict[str, object]]", wm["rows"])
        sc_111 = next(r for r in rows if r["criterion"] == "1.1.1 Non-text Content")
        assert sc_111["verdict"] == "FAIL"

    def test_not_tested_when_no_related_checks_ran(self) -> None:
        out = build_report_sections(_make_ctx(), check_results=[]) 
        wm = cast("dict[str, object]", out["wcag_mapping"])
        rows = cast("list[dict[str, object]]", wm["rows"])
        # 1.1.1 has related checks, but none ran → NOT_TESTED.
        sc_111 = next(r for r in rows if r["criterion"] == "1.1.1 Non-text Content")
        assert sc_111["verdict"] == "NOT_TESTED"
        # 3.2.6 has no related checks → MANUAL.
        sc_326 = next(r for r in rows if r["criterion"] == "3.2.6 Consistent Help")
        assert sc_326["verdict"] == "MANUAL"


# ---------------------------------------------------------------------------
# §16 Version Recommendations (Phase B)
# ---------------------------------------------------------------------------


class TestVersionRecommendations:

    def test_pdf17_yields_upgrade_recommendation(self) -> None:
        import pikepdf
        pdf = pikepdf.Pdf.new()
        # pikepdf.Pdf.new() yields a PDF 1.7 by default — confirm the
        # builder reports it as such.
        out = build_report_sections(_make_ctx()) 
        ctx = _make_ctx()
        ctx.pdf = pdf
        out = build_report_sections(ctx) 
        vr = cast("dict[str, object]", out["version_recommendations"])
        assert vr["is_pdf2"] is False
        assert vr["pdf_version"] is not None

    def test_counts_used_pdf2_tags(self) -> None:
        elements = [
            _make_element(index=0, tag="Aside", text="sidebar"),
            _make_element(index=1, tag="Em", text="emphasised"),
            _make_element(index=2, tag="P", text="body"),
        ]
        out = build_report_sections(_make_ctx(elements=elements)) 
        vr = cast("dict[str, object]", out["version_recommendations"])
        used = cast("list[str]", vr["used_pdf2_tags"])
        assert "Aside" in used
        assert "Em" in used

    def test_suggests_sect_groups_from_top_level_headings(self) -> None:
        elements = [
            _make_element(index=0, tag="H1", text="Intro"),
            _make_element(index=1, tag="P", parent_index=-1, text="body"),
            _make_element(index=2, tag="H1", text="Methods"),
        ]
        out = build_report_sections(_make_ctx(elements=elements)) 
        vr = cast("dict[str, object]", out["version_recommendations"])
        recs = cast("list[dict[str, object]]", vr["section_recommendations"])
        assert len(recs) == 2
        assert recs[0]["heading"] == "Intro"
        assert recs[1]["heading"] == "Methods"


# ---------------------------------------------------------------------------
# Phase C — Executive summary
# ---------------------------------------------------------------------------


class TestExecutiveSummary:

    def test_pass_verdict_with_no_issues(self) -> None:
        from auto_a11y.pdf.models import CheckResult
        crs = [
            CheckResult(name="A", standard="W", result="PASS", details=""),
            CheckResult(name="B", standard="W", result="PASS", details=""),
        ]
        out = build_report_sections(_make_ctx(), check_results=crs)
        es = cast("dict[str, object]", out["executive_summary"])
        assert es["verdict"] == "PASS"
        assert es["fail_count"] == 0
        assert es["top_issues"] == []

    def test_fail_dominates_warn(self) -> None:
        from auto_a11y.pdf.models import CheckResult
        crs = [
            CheckResult(name="X", standard="W", result="FAIL", details=""),
            CheckResult(name="Y", standard="W", result="WARN", details=""),
        ]
        out = build_report_sections(_make_ctx(), check_results=crs)
        es = cast("dict[str, object]", out["executive_summary"])
        assert es["verdict"] == "FAIL"

    def test_top_issues_sorted_by_fail_count(self) -> None:
        from auto_a11y.pdf.models import CheckResult
        crs = [
            CheckResult(name="Many fails", standard="W", result="FAIL", details=""),
            CheckResult(name="Many fails", standard="W", result="FAIL", details=""),
            CheckResult(name="Many fails", standard="W", result="FAIL", details=""),
            CheckResult(name="One fail", standard="W", result="FAIL", details=""),
            CheckResult(name="Just warn", standard="W", result="WARN", details=""),
        ]
        out = build_report_sections(_make_ctx(), check_results=crs)
        es = cast("dict[str, object]", out["executive_summary"])
        top = cast("list[dict[str, object]]", es["top_issues"])
        # "Many fails" leads with 3 FAILs, then "One fail" with 1, then warn-only.
        assert top[0]["name"] == "Many fails"
        assert top[0]["fail"] == 3


# ---------------------------------------------------------------------------
# Phase C — Visual reading order
# ---------------------------------------------------------------------------


class TestVisualReadingOrder:

    def test_empty_when_no_mismatches(self) -> None:
        out = build_report_sections(_make_ctx())
        vro = cast("dict[str, object]", out["visual_reading_order"])
        assert vro["total"] == 0
        assert vro["rows"] == []

    def test_joins_element_index_to_text_preview(self) -> None:
        from auto_a11y.pdf.audit.reading_order import ReadingOrderMismatch
        elements = [
            _make_element(index=0, tag="P", text="First in struct"),
            _make_element(index=1, tag="P", text="Second in struct"),
        ]
        ctx = _make_ctx(elements=elements)
        ctx.reading_order_mismatches = [
            ReadingOrderMismatch(
                struct_first=0,
                struct_second=1,
                visual_first=1,
                visual_second=0,
            )
        ]
        out = build_report_sections(ctx)
        vro = cast("dict[str, object]", out["visual_reading_order"])
        rows = cast("list[dict[str, object]]", vro["rows"])
        assert rows[0]["struct_first_text"] == "First in struct"
        assert rows[0]["visual_first_text"] == "Second in struct"


# ---------------------------------------------------------------------------
# Phase C — Exported Images gallery
# ---------------------------------------------------------------------------


class TestExportedImages:

    def test_pairs_extracted_image_with_matching_figure_alt(self) -> None:
        from auto_a11y.pdf.audit.images import ExtractedImage
        elements = [
            _make_element(index=10, tag="Figure", alt="Diagram of system"),
            _make_element(index=11, tag="Figure"),  # No alt → second image unmatched.
        ]
        extracted = [
            ExtractedImage(index=1, filename="img_1.png", width=400, height=300),
            ExtractedImage(index=2, filename="img_2.png", width=200, height=200),
        ]
        out = build_report_sections(_make_ctx(elements=elements, images=extracted))
        ei = cast("dict[str, object]", out["exported_images"])
        rows = cast("list[dict[str, object]]", ei["rows"])
        assert len(rows) == 2
        assert rows[0]["alt_text"] == "Diagram of system"
        assert rows[0]["has_alt"] is True
        assert rows[0]["linked_struct_index"] == 10
        assert rows[1]["has_alt"] is False


# ---------------------------------------------------------------------------
# Phase C — Images-of-text placeholder
# ---------------------------------------------------------------------------


class TestImagesOfTextPlaceholder:

    def test_marks_unavailable_until_ai_lands(self) -> None:
        out = build_report_sections(_make_ctx())
        iot = cast("dict[str, object]", out["images_of_text"])
        assert iot["available"] is False
        assert iot["reason"] == "ai_not_configured"
