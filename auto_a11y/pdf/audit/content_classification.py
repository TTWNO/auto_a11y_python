"""Classifying page content as tagged, artifact, or neither.

Every mark on a page has to be one of two things: content, reached
through the structure tree by a marked-content id, or an artifact —
decoration, a running header, a rule — explicitly declared as something a
reader can skip. Anything that is neither is invisible to assistive
technology while still being visible on the page, which is the whole
failure this module detects.

The classification is read from the content stream's marked-content
operators (``BDC``/``BMC`` … ``EMC``), which nest, so the state is a
stack rather than a flag.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, TypeAlias

import pikepdf
from pikepdf import Name

logger = logging.getLogger(__name__)

#: Text-showing operators.
_TEXT_OPERATORS = frozenset({"Tj", "TJ", "'", '"'})

#: Draws a named XObject, which may be an image or a form.
_DRAW_XOBJECT = "Do"

#: What a level of marked-content nesting means.
_Context: TypeAlias = Literal["tagged", "artifact", "other"]


@dataclass(frozen=True)
class PageContentClassification:
    """What one page's content stream declared about itself."""

    page_number: int
    artifact_inside_tagged: int
    tagged_inside_artifact: int
    untagged_text_operators: int
    untagged_image_operators: int
    text_operators: int = 0
    """Every text-showing operator on the page, tagged or not.

    Zero here means the page carries no text at all — the signal that
    separates a scan from a document whose text is merely untagged."""
    image_operators: int = 0
    """Every image drawn on the page, tagged or not."""

    @property
    def untagged_operators(self) -> int:
        """Marks on the page that are neither content nor artifact."""
        return self.untagged_text_operators + self.untagged_image_operators


def _image_xobject_names(page: pikepdf.Page) -> frozenset[str]:
    """Names in the page's resources that refer to an image.

    A ``Do`` on a form XObject draws whatever that form contains, and the
    form has its own marked content; only image XObjects put ink on the
    page directly.
    """
    resources = page.obj.get(Name("/Resources"))
    if resources is None:
        return frozenset()
    xobjects = resources.get(Name("/XObject"))
    if xobjects is None:
        return frozenset()

    names: set[str] = set()
    for key in xobjects.keys():
        try:
            entry = xobjects[key]
        except (KeyError, pikepdf.PdfError):
            continue
        subtype = entry.get(Name("/Subtype"))
        if subtype is not None and str(subtype) == "/Image":
            names.add(str(key))
    return frozenset(names)


def _innermost(stack: list[_Context]) -> _Context | None:
    """The nearest enclosing context that means something.

    Marked content that is neither tagged nor an artifact — a ``BDC``
    with no ``/MCID`` — does not change what its children are, so it is
    skipped rather than treated as a boundary.
    """
    for context in reversed(stack):
        if context in ("tagged", "artifact"):
            return context
    return None


def _classify_page(
    page: pikepdf.Page, page_number: int
) -> PageContentClassification:
    try:
        instructions = pikepdf.parse_content_stream(page)
    except (pikepdf.PdfError, ValueError, TypeError) as exc:
        # A stream that will not parse tells us nothing either way; the
        # page is reported as clean rather than as a false violation.
        logger.debug("Page %d content stream did not parse: %s", page_number, exc)
        return PageContentClassification(page_number, 0, 0, 0, 0, 0, 0)

    images = _image_xobject_names(page)
    stack: list[_Context] = []
    artifact_inside_tagged = 0
    tagged_inside_artifact = 0
    untagged_text = 0
    untagged_images = 0
    text_operators = 0
    image_operators = 0

    for instruction in instructions:
        if isinstance(instruction, pikepdf.ContentStreamInlineImage):
            # An inline image draws directly, exactly like a Do on an
            # image XObject.
            image_operators += 1
            if not stack:
                untagged_images += 1
            continue

        operator = str(instruction.operator)
        operands = instruction.operands

        if operator in ("BDC", "BMC"):
            tag = str(operands[0]) if operands else ""
            properties = operands[1] if len(operands) > 1 else None
            has_mcid = (
                isinstance(properties, pikepdf.Dictionary)
                and properties.get(Name("/MCID")) is not None
            )

            if tag == "/Artifact":
                if _innermost(stack) == "tagged":
                    artifact_inside_tagged += 1
                stack.append("artifact")
            elif has_mcid:
                if _innermost(stack) == "artifact":
                    tagged_inside_artifact += 1
                stack.append("tagged")
            else:
                stack.append("other")

        elif operator == "EMC":
            if stack:
                stack.pop()

        elif operator in _TEXT_OPERATORS:
            text_operators += 1
            if not stack:
                untagged_text += 1

        elif operator == _DRAW_XOBJECT:
            name = str(operands[0]) if operands else ""
            if name not in images:
                continue
            image_operators += 1
            if not stack:
                untagged_images += 1

    return PageContentClassification(
        page_number=page_number,
        artifact_inside_tagged=artifact_inside_tagged,
        tagged_inside_artifact=tagged_inside_artifact,
        untagged_text_operators=untagged_text,
        untagged_image_operators=untagged_images,
        text_operators=text_operators,
        image_operators=image_operators,
    )


def classify_content(pdf: pikepdf.Pdf) -> list[PageContentClassification]:
    """Classify every page's content. One entry per page, in order.

    Divergence from pdfMax's equivalent, which counts only text
    operators as unclassified. An untagged image is exactly as invisible
    to a screen reader as untagged text, and counting only text is why a
    scanned document — whose every page is one untagged image and no text
    at all — passed the check that exists to catch it.
    """
    return [
        _classify_page(page, number)
        for number, page in enumerate(pdf.pages, start=1)
    ]
