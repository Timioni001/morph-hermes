"""`hermes morph connect` — wiring an API without editing JSON."""
from __future__ import annotations

import argparse
import json

import pytest

from morph import cli, connect, registry
from morph.config import reload_settings, settings
from morph.errors import ConfigError


def _saved(home):
    return json.loads((home / "config.json").read_text(encoding="utf-8"))


def _run(*argv):
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    return cli.run(parser.parse_args(list(argv)))


def test_an_unconfigured_morph_tells_you_how_to_connect_something(morph_home, capsys):
    assert "hermes morph connect github" in cli.status_text()
    assert _run("up") == 0
    assert "connect sandbox" in capsys.readouterr().out


def test_connecting_the_sandbox_installs_three_applications_and_their_joins(morph_home):
    result = connect.add_sandbox()
    assert result["connected"] == ["storefront", "helpdesk", "warehouse"]
    saved = _saved(morph_home)
    assert len(saved["links"]) == 4 and saved["allow_loopback"] is True
    assert {c.id for c in settings().connectors} == {"storefront", "helpdesk", "warehouse"}
    # A second run neither duplicates nor silently overwrites.
    with pytest.raises(ConfigError):
        connect.add_sandbox()
    connect.add_sandbox(replace=True)
    assert len(_saved(morph_home)["connectors"]) == 3
    assert len(_saved(morph_home)["links"]) == 4


def test_a_connection_that_does_not_answer_is_not_saved(morph_home):
    """Loopback port 9 answers nobody; the file must be exactly as it was."""
    with pytest.raises(ConfigError) as refused:
        connect.add_openapi("http://127.0.0.1:9", connector_id="ghost")
    assert "Nothing was changed" in refused.value.message
    assert not (morph_home / "config.json").exists()
    assert settings().connectors == ()

    connect.add_sandbox()
    before = (morph_home / "config.json").read_text(encoding="utf-8")
    with pytest.raises(ConfigError):
        connect.add_openapi("http://127.0.0.1:9", connector_id="ghost")
    assert (morph_home / "config.json").read_text(encoding="utf-8") == before
    assert "ghost" not in {c.id for c in settings().connectors}


def test_keep_saves_an_unreachable_connection_on_request(morph_home):
    result = connect.add_openapi("http://127.0.0.1:9", connector_id="later",
                                 keep_unreachable=True)
    assert result["probes"][0]["ok"] is False
    entry = _saved(morph_home)["connectors"][0]
    assert entry["kind"] == "openapi" and entry["base_url"] == "http://127.0.0.1:9"


def test_a_github_repository_is_named_owner_slash_name(morph_home):
    for bad in ("", "no-slash", "../etc/passwd", "a/b/c"):
        with pytest.raises(ConfigError):
            connect.add_github(bad)
    assert not (morph_home / "config.json").exists()


def test_connecting_github_records_the_variable_name_never_a_token(morph_home, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_this_must_not_be_written")
    monkeypatch.setattr(registry, "health", lambda: [])   # no network in a unit test
    connect.add_github("https://github.com/acme/widgets/")
    text = (morph_home / "config.json").read_text(encoding="utf-8")
    assert "ghp_this_must_not_be_written" not in text
    saved = _saved(morph_home)
    assert saved["connectors"] == [
        {"id": "widgets", "kind": "github", "label": "acme/widgets",
         "token_env": "GITHUB_TOKEN", "read_only": False, "timeout": 12.0,
         "options": {"repo": "acme/widgets"}}]
    assert "api.github.com" in saved["allowed_hosts"]
    with pytest.raises(ConfigError):
        connect.add_github("acme/widgets")            # same id, no --replace
    connect.add_github("other/widgets", connector_id="other_widgets")
    assert [c.id for c in settings().connectors] == ["widgets", "other_widgets"]


def test_disconnecting_removes_the_connection_and_its_joins(morph_home):
    connect.add_sandbox()
    result = connect.remove("warehouse")
    assert result["links_removed"]
    assert {c.id for c in settings().connectors} == {"storefront", "helpdesk"}
    assert not any("warehouse." in json.dumps(link) for link in _saved(morph_home)["links"])
    with pytest.raises(ConfigError):
        connect.remove("warehouse")


def test_connect_will_not_edit_a_file_that_is_being_outranked(morph_home, monkeypatch):
    """Inline configuration wins, so editing the file would be a silent no-op."""
    monkeypatch.setenv("MORPH_CONFIG", json.dumps({"connectors": []}))
    reload_settings()
    registry.reset()
    with pytest.raises(ConfigError) as refused:
        connect.add_github("acme/widgets", keep_unreachable=True)
    assert "outranks" in refused.value.message
    assert not (morph_home / "config.json").exists()


def test_the_command_line_connects_lists_and_disconnects(morph_home, capsys):
    assert _run("connect", "sandbox") == 0
    assert "hermes morph up" in capsys.readouterr().out
    assert _run("connections") == 0
    listed = capsys.readouterr().out
    assert "storefront" in listed and "warehouse" in listed
    assert _run("disconnect", "helpdesk") == 0
    assert "helpdesk" not in {c.id for c in settings().connectors}
    assert _run("connect", "openapi", "http://127.0.0.1:9", "--id", "ghost") != 0
    assert "Nothing was changed" in capsys.readouterr().err
