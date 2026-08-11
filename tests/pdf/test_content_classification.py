"""Tests for page content classification.

The case this exists for is the scanned document. Every mark on a page
must be either tagged content or a declared artifact; anything else is
visible on the page and invisible to assistive technology. A checker that
counts only text operators reports a scan — one untagged image per page,
no text at all — as clean, which is the failure these tests pin.
"""
from __future__ import annotations

import zlib

import pikepdf
from pikepdf import Dictionary, Name

from auto_a11y.pdf.audit.content_classification import classify_content


def _page_with(content: bytes, *, with_image: bool = False) -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    resources: dict[str, object] = {}
    if with_image:
        image = pikepdf.Stream(pdf, zlib.compress(bytes(100 * 100)))
        image.Type = Name("/XObject")
        image.Subtype = Name("/Image")
        image.Width = 100
        image.Height = 100
        image.ColorSpace = Name("/DeviceGray")
        image.BitsPerComponent = 8
        image.Filter = Name("/FlateDecode")
        resources["/XObject"] = Dictionary(Im0=pdf.make_indirect(image))
    font = pdf.make_indirect(
        Dictionary(Type=Name("/Font"), Subtype=Name("/Type1"),
                   BaseFont=Name("/Helvetica"))
    )
    resources["/Font"] = Dictionary(F1=font)
    page.obj[Name("/Resources")] = Dictionary(resources)
    page.obj[Name("/Contents")] = pikepdf.Stream(pdf, content)
    return pdf


_TEXT = b"BT /F1 12 Tf 20 100 Td (Hello) Tj ET"
_IMAGE = b"q 100 0 0 100 0 0 cm /Im0 Do Q"


def test_untagged_text_is_counted() -> None:
    (page,) = classify_content(_page_with(_TEXT))

    assert page.untagged_text_operators == 1
    assert page.untagged_operators == 1


def test_untagged_images_are_counted() -> None:
    """The divergence that makes a scanned page fail.

    pdfMax counts only text operators, so a page whose sole content is
    an untagged image reports clean — which is every page of a scan.
    """
    (page,) = classify_content(_page_with(_IMAGE, with_image=True))

    assert page.untagged_image_operators == 1
    assert page.untagged_text_operators == 0
    assert page.untagged_operators == 1


def test_tagged_text_is_not_counted() -> None:
    content = b"/P <</MCID 0>> BDC " + _TEXT + b" EMC"

    (page,) = classify_content(_page_with(content))

    assert page.untagged_operators == 0


def test_tagged_images_are_not_counted() -> None:
    content = b"/Figure <</MCID 0>> BDC " + _IMAGE + b" EMC"

    (page,) = classify_content(_page_with(content, with_image=True))

    assert page.untagged_operators == 0


def test_artifact_content_is_not_counted() -> None:
    # A declared artifact is content a reader is meant to skip, which is
    # a legitimate classification rather than an omission.
    content = b"/Artifact BMC " + _TEXT + b" EMC"

    (page,) = classify_content(_page_with(content))

    assert page.untagged_operators == 0


def test_an_artifact_inside_tagged_content_is_reported() -> None:
    content = (
        b"/P <</MCID 0>> BDC /Artifact BMC " + _TEXT + b" EMC EMC"
    )

    (page,) = classify_content(_page_with(content))

    assert page.artifact_inside_tagged == 1


def test_tagged_content_inside_an_artifact_is_reported() -> None:
    content = (
        b"/Artifact BMC /P <</MCID 0>> BDC " + _TEXT + b" EMC EMC"
    )

    (page,) = classify_content(_page_with(content))

    assert page.tagged_inside_artifact == 1


def test_marked_content_without_an_mcid_is_transparent() -> None:
    """A BDC with no /MCID is neither tagged nor an artifact.

    It must not act as a boundary, or content inside it would be
    classified by the wrapper rather than by its own marking.
    """
    content = (
        b"/Span BDC /Artifact BMC " + _TEXT + b" EMC EMC"
    )

    (page,) = classify_content(_page_with(content))

    assert page.artifact_inside_tagged == 0, "the Span is not tagged content"
    assert page.untagged_operators == 0


def test_pages_are_numbered_from_one() -> None:
    pdf = _page_with(_TEXT)
    pdf.add_blank_page(page_size=(200, 200))

    pages = classify_content(pdf)

    assert [p.page_number for p in pages] == [1, 2]


def test_an_unparsable_stream_reports_nothing_rather_than_a_violation() -> None:
    # A stream we cannot read tells us nothing either way; inventing a
    # failure from it would be worse than staying quiet.
    pdf = _page_with(b"this is not a content stream (((")

    (page,) = classify_content(pdf)

    assert page.untagged_operators == 0
