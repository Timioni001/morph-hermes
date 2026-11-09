"""The dashboard's stylesheet and bundle, checked as data rather than run.

The browser suite covers behaviour. These cover the two things it cannot see: a
palette that exists twice and could drift, and a bundle that could fall behind
the sources it is built from.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STYLE = ROOT / "dashboard" / "src" / "style.css"
DIST = ROOT / "dashboard" / "dist" / "index.js"
SRC = ROOT / "dashboard" / "src"

TOKEN = re.compile(r"(--mo-[a-z0-9-]+)\s*:")


def _block(css: str, selector: str) -> str:
    """The body of the first rule whose selector line contains *selector*."""
    start = css.index(selector)
    opening = css.index("{", start) + 1
    depth, i = 1, opening
    while depth:
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
        i += 1
    return css[opening:i - 1]


def test_both_dark_palettes_define_the_same_tokens():
    """The dark palette exists twice: once for the OS preference, once explicit.

    CSS cannot share one block between a media query and a plain selector, so
    they are written out twice. A token added to one and not the other would
    leave the dashboard half-themed inside a dark host, which is exactly the
    failure the explicit block was added to fix.
    """
    css = STYLE.read_text(encoding="utf-8")
    by_preference = set(TOKEN.findall(_block(css, '.morph:not([data-mo-theme="light"])')))
    explicit = set(TOKEN.findall(_block(css, '.morph[data-mo-theme="dark"]')))
    assert by_preference, "the preference-driven dark palette disappeared"
    assert by_preference == explicit, (
        "the two dark palettes have drifted; "
        f"only in the media query: {sorted(by_preference - explicit)}; "
        f"only in the explicit block: {sorted(explicit - by_preference)}")


DECLARATION = re.compile(r"(--mo-[a-z0-9-]+)\s*:\s*([^;]+);")
COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}|rgba?\(|hsla?\(|color-mix\(")


def test_every_colour_the_light_palette_defines_has_a_dark_counterpart():
    """Geometry and easing are theme-independent; colour is not."""
    css = STYLE.read_text(encoding="utf-8")
    light = {name: value for name, value in DECLARATION.findall(_block(css, ".morph {"))}
    dark = set(TOKEN.findall(_block(css, '.morph[data-mo-theme="dark"]')))
    colours = {name for name, value in light.items() if COLOUR.search(value)}
    assert colours, "the light palette defines no colours at all"
    assert not (colours - dark), f"no dark value for: {sorted(colours - dark)}"


def test_the_committed_bundle_is_not_older_than_its_sources():
    """`make bundle` runs the real check; this catches a commit that forgot to."""
    if not DIST.exists():
        pytest.skip("no built bundle in this checkout")
    newest = max(p.stat().st_mtime for p in SRC.glob("*.js"))
    assert DIST.stat().st_mtime >= newest - 1, (
        "dashboard/dist/index.js is older than dashboard/src/*.js — "
        "run `node scripts/build_dashboard.mjs`")


def test_the_bundle_carries_no_hardcoded_blue_from_the_old_palette():
    """The accent moved to near-black; a stray literal would reintroduce it."""
    text = DIST.read_text(encoding="utf-8") if DIST.exists() else ""
    for stale in ("#1d4ed8", "#5b86ff", "#6d94fc", "#6f96ff"):
        assert stale not in text, f"{stale} is back in the bundle"


def test_the_plugin_never_writes_to_the_host_s_own_markup():
    """Catalog rules 8 and 9: a plugin extends the host, it does not restyle it.

    Theme detection reads `document.documentElement`; writing to it would change
    the Hermes shell itself.
    """
    sources = "\n".join(p.read_text(encoding="utf-8") for p in sorted(SRC.glob("*.js")))
    for forbidden in ("documentElement.dataset", "documentElement.setAttribute",
                      "documentElement.className", "documentElement.style",
                      "document.body.className", "document.body.setAttribute"):
        assert forbidden not in sources, f"the dashboard writes to host markup: {forbidden}"
