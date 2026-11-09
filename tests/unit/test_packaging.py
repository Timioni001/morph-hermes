"""The plugin package itself: manifests, the committed bundle, and the docs that
promise things about them."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def manifest() -> dict:
    return yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))


def test_plugin_yaml_declares_what_the_code_registers():
    from morph import schemas
    data = manifest()
    assert data["name"] == "morph"
    assert sorted(data["provides_tools"]) == sorted(schemas.TOOLS)
    assert data["requires_hermes"].startswith(">=")
    assert data["license"] and data["description"]


def test_the_dashboard_manifest_matches_the_plugin():
    data = json.loads((ROOT / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    assert data["name"] == manifest()["name"]
    assert data["version"] == manifest()["version"]
    for key in ("entry", "css", "api"):
        assert (ROOT / "dashboard" / data[key]).is_file(), key
    assert data["tab"]["path"] == "/morph"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_committed_dashboard_bundle_matches_its_sources():
    result = subprocess.run([shutil.which("node"), "scripts/build_dashboard.mjs", "--check"],
                            cwd=str(ROOT), capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_dashboard_bundle_parses_and_registers_itself():
    bundle = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
    result = subprocess.run([shutil.which("node"), "--check",
                             str(ROOT / "dashboard" / "dist" / "index.js")],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'register("morph", MorphPage)' in bundle
    assert "window.__HERMES_PLUGINS__" in bundle
    assert "window.__HERMES_PLUGIN_SDK__" in bundle


def test_the_dashboard_bundle_cannot_execute_anything_it_is_handed():
    """Scanned with comments removed, so the file may describe what it does not do."""
    import re
    bundle = (ROOT / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", bundle, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    for forbidden in ("innerHTML", "dangerouslySetInnerHTML", "eval(", "new Function",
                      "document.write", "insertAdjacentHTML", "javascript:",
                      "importScripts", "<script"):
        assert forbidden not in code, forbidden


def test_the_catalog_entry_is_consistent_with_the_manifest():
    entry = yaml.safe_load((ROOT / "catalog-entry.yaml").read_text(encoding="utf-8"))
    data = manifest()
    assert entry["name"] == data["name"]
    assert entry["version"] == str(data["version"])
    assert entry["requires_hermes"] == data["requires_hermes"]
    assert entry["repo"].startswith("https://github.com/")
    assert entry["capabilities"]["provides_tools"] == data["provides_tools"]
    assert entry["capabilities"]["provides_hooks"] == data["provides_hooks"]
    assert entry["capabilities"]["requires_env"] == []


def test_no_binary_or_bytecode_is_committed():
    """Catalog rule 17: everything shipped must be readable source.

    Skipped outside a git checkout — a source tarball has no index to ask, and the
    rule is about what the catalog clones, which is always a checkout.
    """
    magic = (b"\x7fELF", b"MZ", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\x00asm")
    offenders = []
    listing = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                             text=True, check=False)
    if listing.returncode != 0:
        pytest.skip("not a git checkout, so there is no committed file list to audit")
    tracked = listing.stdout.split()
    for name in tracked:
        path = ROOT / name
        if not path.is_file():
            continue
        if path.suffix in (".pyc", ".pyo"):
            offenders.append(name)
            continue
        with path.open("rb") as handle:
            head = handle.read(8)
        if head.startswith(magic):
            offenders.append(name)
    assert not offenders, offenders


def test_the_environment_template_documents_every_token_the_default_config_needs():
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    default = json.loads((ROOT / "config" / "connectors.default.json").read_text(encoding="utf-8"))
    for connector in default["connectors"]:
        if connector.get("token_env"):
            assert connector["token_env"] in template, connector["token_env"]
