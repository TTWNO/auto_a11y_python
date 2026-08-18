"""``requirements-windows.txt`` must stay in step with ``requirements.txt``.

The Windows build cross-installs from macOS, which means pip resolves
wheels for a platform it is not running on
(``--platform win_amd64 --only-binary=:all:``). That mode refuses any
package with no Windows wheel, so the one pinned package that publishes
only an sdist is commented out of the Windows list and unpacked by the
build instead.

Two lists is a liability: add a dependency, forget the second file, and
the Windows build ships without it — discovered when someone launches the
installer, if at all. This test is what makes the split safe.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_BASE = REPO_ROOT / "requirements.txt"
_WINDOWS = REPO_ROOT / "requirements-windows.txt"

#: Packages deliberately absent from the Windows list, and why. The build
#: installs each one by another route; a name here without that handling
#: is a package that silently vanishes from the bundle.
_HANDLED_SEPARATELY = {
    "wcag-contrast-ratio": "sdist only; the build builds a wheel from it",
}

#: Packages the Windows list carries that requirements.txt does not, and
#: why. pip resolves wheels for ``--platform`` but evaluates environment
#: markers against the *host*, so a dependency guarded by
#: ``platform_system == "Windows"`` is dropped without a warning when
#: cross-installing from macOS. Naming it here is what puts it back.
#:
#: Nothing needs these on macOS or Linux, which is why adding them to
#: requirements.txt would be wrong.
_WINDOWS_ONLY = {
    "tzdata": (
        "Windows has no system IANA database; zoneinfo falls back "
        + "to this. Required by tzlocal and pandas."
    ),
    "win32-setctime": "loguru imports it unguarded under os.name == 'nt'.",
}

#: Any requirement line, not only ``==`` ones. Matching pins alone let
#: ``openapi-spec-validator>=0.7.1,<0.9`` through unseen — a requirement
#: the guard was blind to is exactly the one that goes missing quietly.
#: Options lines (``-r``, ``--index-url``) start with ``-`` and so do not
#: match.
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[<>=!~@\[;]|$)")


def _pins(path: Path) -> dict[str, str]:
    """Package name to its whole requirement line, ignoring comments and
    blanks. Environment markers and extras stay part of the value, so a
    difference in either counts as a disagreement."""
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _REQUIREMENT.match(stripped)
        if match:
            pins[match.group(1).lower()] = stripped
    return pins


def test_the_windows_list_exists() -> None:
    assert _WINDOWS.is_file(), "the Windows build reads this file"


def test_every_requirement_is_in_the_windows_list() -> None:
    """Except the ones the build installs another way."""
    base = _pins(_BASE)
    windows = _pins(_WINDOWS)
    handled = {name.lower() for name in _HANDLED_SEPARATELY}

    missing = sorted(set(base) - set(windows) - handled)

    assert not missing, (
        "these are in requirements.txt but not in requirements-windows.txt, "
        f"so the Windows build would ship without them: {missing}"
    )


def test_the_windows_list_adds_nothing_undocumented() -> None:
    """A Windows-only dependency goes untested everywhere else, so each
    one has to be a deliberate, explained entry rather than a stray."""
    allowed = {name.lower() for name in _WINDOWS_ONLY}
    extra = sorted(set(_pins(_WINDOWS)) - set(_pins(_BASE)) - allowed)

    assert not extra, (
        "only in requirements-windows.txt and not explained in "
        f"_WINDOWS_ONLY: {extra}"
    )


def test_every_windows_only_package_is_actually_listed() -> None:
    """The reverse: an entry documented here but missing from the file
    means the dependency it describes is not being staged."""
    windows = _pins(_WINDOWS)

    absent = sorted(
        name for name in _WINDOWS_ONLY if name.lower() not in windows
    )

    assert not absent, (
        "documented as Windows-only but not in requirements-windows.txt, "
        f"so the Windows build ships without them: {absent}"
    )


def test_the_requirements_agree_line_for_line() -> None:
    """Same package, same version. A drift here is the hardest kind to
    find: the app behaves differently on Windows for no visible reason."""
    base = _pins(_BASE)
    windows = _pins(_WINDOWS)

    disagreements = [
        f"{name}: {base[name]} vs {windows[name]}"
        for name in sorted(set(base) & set(windows))
        if base[name] != windows[name]
    ]

    assert not disagreements, disagreements


def test_each_excluded_package_is_named_in_the_build() -> None:
    """The exclusion is only safe because the build handles it."""
    script = (REPO_ROOT / "build" / "build-windows.sh").read_text(
        encoding="utf-8"
    )

    for name in _HANDLED_SEPARATELY:
        assert name in script, (
            f"{name} is excluded from the Windows requirements but the "
            "build does not mention it — it would simply be missing"
        )
