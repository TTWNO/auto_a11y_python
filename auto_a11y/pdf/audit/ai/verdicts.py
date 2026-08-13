"""Turn AI analysis output into check verdicts.

pdfMax's AI functions each return a :class:`CheckResult` alongside their
report section, so an AI run changes the pass/fail counts rather than living
in a sidebar of its own. This module does the same for the ported passes, so
a scan with AI enabled reports the same checks pdfMax reports.

Three verdicts belong to the AI alone — nothing deterministic produces
them, so they exist only when AI runs:

* ``Alt text adequacy`` (WCAG 1.1.1) — from :func:`..vision.assess_alt_text`
* ``Images of text have matching alt text`` (WCAG 1.4.5) — from
  :func:`..vision.detect_images_of_text`
* ``Use of color not sole indicator`` (WCAG 1.4.1) — from
  :func:`..vision.analyze_color_use`

Two more *supersede* a deterministic verdict of the same name, which the
pipeline resolves by replacement rather than by appending both:

* ``Non-text contrast sufficient`` (WCAG 1.4.11) — from
  :func:`..forms_visual.analyze_non_text_contrast`, under the section key
  ``non_text_contrast_ai``: the deterministic measurements own
  ``non_text_contrast``, and the report shows both
* ``Required fields visually indicated`` (WCAG 3.3.2, 1.3.1) — from
  :func:`..forms_visual.analyze_required_indicators`

Superseding is the honest resolution for those two: the deterministic
check says in its own details that it cannot see the page, so once
something has, its answer is the better one. pdfMax does the same thing
by dropping the earlier result before appending the AI one (line ~11693).

Verdicts in the first group degrade to ``INFO`` rather than ``PASS``
when their pass produced no data. A check that could not run has not
found the document compliant, and recording it as a pass would inflate
the score for exactly the documents nobody examined. Verdicts in the
second group return nothing at all when their pass produced no data, so
the deterministic verdict stands unchanged.
"""
from __future__ import annotations

from typing import Any, cast

from auto_a11y.pdf.models import CheckResult

__all__ = ["derive_check_results"]


def derive_check_results(sections: dict[str, object]) -> list[CheckResult]:
    """Build the AI-derived check verdicts from the analysis sections."""
    results: list[CheckResult] = [
        _alt_text_verdict(_section(sections, "alt_text_adequacy")),
        _images_of_text_verdict(_section(sections, "images_of_text")),
        _color_use_verdict(_section(sections, "color_use")),
    ]
    contrast = _non_text_contrast_verdict(
        _section(sections, "non_text_contrast_ai")
    )
    if contrast is not None:
        results.append(contrast)
    required = _required_indicator_verdict(
        _section(sections, "required_indicators")
    )
    if required is not None:
        results.append(required)
    return results


def _non_text_contrast_verdict(
    data: dict[str, Any] | None,
) -> CheckResult | None:
    """Supersede the measured non-text contrast verdict with the seen one."""
    if data is None:
        return None
    name, standard = "Non-text contrast sufficient", "WCAG 1.4.11"
    findings = _findings(data)
    fails = [f for f in findings if f.get("severity") == "fail"]
    warnings = [f for f in findings if f.get("severity") == "warning"]

    if fails:
        return CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"AI visual analysis found {len(fails)} non-text contrast"
                " failure(s). See Non-text Contrast section for details"
            ),
        )
    if warnings:
        return CheckResult(
            name=name, standard=standard, result="WARN",
            details=(
                f"AI analysis flagged {len(warnings)} potential concern(s)."
                " See Non-text Contrast section for details"
            ),
        )
    if data.get("pass") is True:
        return CheckResult(
            name=name, standard=standard, result="PASS",
            details=(
                "Form field borders and graphical elements meet 3:1 contrast;"
                " AI confirmed visual adequacy"
            ),
        )
    return None


def _required_indicator_verdict(
    data: dict[str, Any] | None,
) -> CheckResult | None:
    """Supersede the metadata-only required-field verdict with the seen one."""
    if data is None:
        return None
    name, standard = "Required fields visually indicated", "WCAG 3.3.2, 1.3.1"
    results = _list_of_dicts(data.get("field_results"))
    visual_only = _list_of_dicts(data.get("visual_only_required"))
    total = len(results)

    if data.get("pass") is True:
        legend = data.get("legend_text")
        return CheckResult(
            name=name, standard=standard, result="PASS",
            details=(
                f"AI confirmed all {total} required field(s) have visual"
                + " indicators"
                + (
                    f' with legend: "{legend}"'
                    if isinstance(legend, str) and legend else ""
                )
            ),
        )

    missing = [r for r in results if not r.get("has_visual_indicator")]
    if missing:
        names = ", ".join(
            str(r.get("field_name") or "unnamed") for r in missing[:5]
        )
        return CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"{len(missing)} required field(s) lack visual indicators:"
                + f" {names}"
                + (
                    f". Also {len(visual_only)} field(s) visually marked"
                    + " required but missing /Ff flag" if visual_only else ""
                )
            ),
        )
    if visual_only:
        descriptions = "; ".join(
            str(v.get("description") or "") for v in visual_only[:3]
        )
        return CheckResult(
            name=name, standard=standard, result="WARN",
            details=(
                f"All {total} required field(s) have visual indicators, but"
                f" {len(visual_only)} field(s) appear visually required"
                f" without the semantic /Ff Required flag: {descriptions}"
            ),
        )
    assessment = data.get("overall_assessment")
    return CheckResult(
        name=name, standard=standard, result="WARN",
        details=(
            "AI analysis indicated issues with required field indicators."
            + (f" {assessment}" if isinstance(assessment, str) else "")
        ),
    )


def _findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    return _list_of_dicts(data.get("findings"))


def _list_of_dicts(raw: object) -> list[dict[str, Any]]:
    """Every dict in a list-shaped payload; empty for anything else."""
    if not isinstance(raw, list):
        return []
    return [
        cast("dict[str, Any]", item)
        for item in cast("list[object]", raw)
        if isinstance(item, dict)
    ]


def _section(sections: dict[str, object], key: str) -> dict[str, Any] | None:
    value = sections.get(key)
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return None


def _alt_text_verdict(data: dict[str, Any] | None) -> CheckResult:
    name, standard = "Alt text adequacy", "WCAG 1.1.1"
    if data is None:
        return CheckResult(
            name=name, standard=standard, result="INFO",
            details="Alt text adequacy was not assessed by the AI analysis.",
        )

    total = _int(data.get("total"))
    inadequate = _int(data.get("inadequate"))
    errors = _int(data.get("errors"))
    scored = total - errors

    if total == 0:
        return CheckResult(
            name=name, standard=standard, result="NA",
            details="No figures with alt text to evaluate",
        )
    if inadequate:
        detail = f"{inadequate}/{total} figure(s) have inadequate alt text"
        if errors:
            detail += f" ({errors} could not be assessed)"
        return CheckResult(
            name=name, standard=standard, result="WARN", details=detail,
        )
    if errors and scored == 0:
        return CheckResult(
            name=name, standard=standard, result="WARN",
            details=f"Could not assess any of {total} figure(s)",
        )
    detail = f"{scored}/{total} figure alt text(s) assessed as adequate"
    if errors:
        detail += f" ({errors} could not be assessed)"
    return CheckResult(name=name, standard=standard, result="PASS", details=detail)


def _images_of_text_verdict(data: dict[str, Any] | None) -> CheckResult:
    name, standard = "Images of text have matching alt text", "WCAG 1.4.5"
    if data is None or "total_findings" not in data:
        return CheckResult(
            name=name, standard=standard, result="INFO",
            details=(
                "Images of text were not analysed — AI analysis did not run "
                "for this document."
            ),
        )

    findings = _int(data.get("total_findings"))
    checked = len(_list_of_dicts(data.get("images")))
    if findings == 0:
        return CheckResult(
            name=name, standard=standard, result="PASS",
            details=(
                "No images of text found without matching alt text "
                f"({checked} image(s) checked)"
            ),
        )
    return CheckResult(
        name=name, standard=standard, result="WARN",
        details=(
            f"{findings} potential image(s) of text detected — review alt "
            "text coverage"
        ),
    )


def _color_use_verdict(data: dict[str, Any] | None) -> CheckResult:
    name, standard = "Use of color not sole indicator", "WCAG 1.4.1"
    if data is None:
        return CheckResult(
            name=name, standard=standard, result="INFO",
            details=(
                "Use of colour was not analysed — AI analysis did not run "
                "for this document."
            ),
        )

    raw = data.get("findings")
    findings = cast("list[object]", raw) if isinstance(raw, list) else []
    fails = 0
    warns = 0
    for item in findings:
        if not isinstance(item, dict):
            continue
        if cast("dict[str, Any]", item).get("severity") == "fail":
            fails += 1
        else:
            warns += 1

    if fails:
        return CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"{fails} instance(s) where colour alone conveys information"
                + (f", plus {warns} partial case(s)" if warns else "")
            ),
        )
    if warns:
        return CheckResult(
            name=name, standard=standard, result="WARN",
            details=(
                f"{warns} instance(s) where colour is the primary indicator "
                "but some redundancy may exist"
            ),
        )
    return CheckResult(
        name=name, standard=standard, result="PASS",
        details="No content found that relies on colour alone to convey meaning",
    )


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
