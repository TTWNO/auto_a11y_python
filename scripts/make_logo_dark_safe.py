"""Generate a dark-mode-safe CNIB Access Labs logo.

The brand logo's "CNIB" / "ACCESS LABS" text and line art are black on a
transparent background, so they disappear when the page background is dark
(e.g. Word's dark reading mode). This adds a pale-yellow outline (halo)
around every dark shape so the artwork stays legible on both light and dark
backgrounds, then writes cnib-access-labs-logo-dark-safe.png alongside the
original.

    python scripts/make_logo_dark_safe.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "docs" / "images" / "cnib-access-labs-logo.png"
DST = REPO / "docs" / "images" / "cnib-access-labs-logo-dark-safe.png"

PALE_YELLOW = (251, 241, 169)  # #FBF1A9 — light tint of the brand yellow
DARK_LUMA = 90                 # pixels darker than this (and opaque) get a halo
BLUR_RADIUS = 16               # halo thickness (source is ~2600px wide)
HALO_THRESHOLD = 28            # blurred-mask cutoff → crisp outline edge


def main() -> None:
    logo = Image.open(SRC).convert("RGBA")
    r, g, b, a = logo.split()
    luma = Image.merge("RGB", (r, g, b)).convert("L")

    # Mask of the dark, opaque line art / text.
    dark = Image.eval(luma, lambda p: 255 if p < DARK_LUMA else 0)
    opaque = Image.eval(a, lambda p: 255 if p > 128 else 0)
    mask = Image.new("L", logo.size, 0)
    mask.paste(dark, (0, 0), opaque)

    # Grow the mask into a halo: blur, then threshold for a clean edge.
    halo = mask.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
    halo = Image.eval(halo, lambda p: 255 if p > HALO_THRESHOLD else 0)

    # Pale-yellow layer keyed by the halo, composited *behind* the logo so the
    # outline rings each dark shape (and is hidden where the logo paints over).
    outline = Image.new("RGBA", logo.size, PALE_YELLOW + (0,))
    outline.putalpha(halo)
    result = Image.alpha_composite(outline, logo)

    result.save(DST)
    print(f"wrote {DST}")


if __name__ == "__main__":
    main()
