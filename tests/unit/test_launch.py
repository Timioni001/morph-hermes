"""``hermes morph up``: what MORPH may start, and what it must leave alone.

The decisions worth pinning here are the ones that were wrong first time. ``up``
surveys before it starts, and an unauthenticated probe cannot tell a running
application from a stopped one — so if the survey runs before the credentials are
loaded, ``up`` reads a live application as dead and starts a second copy of it on
a port that is already bound.
"""
from __future__ import annotations

import json

import pytest

from morph import launch
from morph.config import get_secret, reload_settings


def _configure(monkeypatch: pytest.MonkeyPatch, connectors: list[dict]) -> None:
    monkeypatch.setenv("MORPH_CONFIG", json.dumps({
        "allow_loopback": True,
        "allowed_hosts": ["127.0.0.1", "localhost"],
        "connectors": connectors,
        "links": [],
        "default_role": "operator",
    }))
    reload_settings()


BUNDLED = [
    {"id": "storefront", "kind": "http-rest", "label": "Storefront",
     "base_url": "http://127.0.0.1:8811", "token_env": "MORPH_STOREFRONT_TOKEN"},
]


def test_a_bundled_application_that_is_down_is_ours_to_start(monkeypatch):
    _configure(monkeypatch, BUNDLED)
    plans = launch.survey(probes=[{"id": "storefront", "ok": False, "error": "refused"}])
    assert [p.state for p in plans] == [launch.STARTABLE]
    assert plans[0].service == "storefront"
    assert plans[0].actionable


def test_a_bundled_application_that_answers_is_left_alone(monkeypatch):
    _configure(monkeypatch, BUNDLED)
    plans = launch.survey(probes=[{"id": "storefront", "ok": True,
                                   "entities": ["orders", "customers"]}])
    assert plans[0].state == launch.RUNNING
    assert not plans[0].actionable


def test_only_a_loopback_url_can_be_ours_to_start():
    """Matching by connector id alone would start a local copy of a remote service.

    Configuration refuses a non-loopback bundled URL before `up` ever sees it —
    plain HTTP off loopback, and private or reserved addresses, are both rejected
    by the network policy — so this pins the matcher that backs that up.
    """
    assert launch._is_loopback("http://127.0.0.1:8811") == (True, 8811)
    assert launch._is_loopback("http://localhost:8811") == (True, 8811)
    assert launch._is_loopback("https://storefront.example.com:8811")[0] is False
    assert launch._is_loopback("https://10.1.2.3:8811")[0] is False
    assert launch._is_loopback("https://example.com")[1] == 443


def test_the_same_application_on_another_port_is_not_ours(monkeypatch):
    _configure(monkeypatch, [{**BUNDLED[0], "base_url": "http://127.0.0.1:9999"}])
    plans = launch.survey(probes=[{"id": "storefront", "ok": False, "error": "refused"}])
    assert plans[0].state == launch.EXTERNAL
    assert "serves 8811" in plans[0].detail


def test_an_unknown_connector_is_never_startable(monkeypatch):
    _configure(monkeypatch, [{"id": "acme", "kind": "http-rest", "label": "Acme",
                              "base_url": "http://127.0.0.1:8811"}])
    plans = launch.survey(probes=[{"id": "acme", "ok": False, "error": "refused"}])
    assert plans[0].state == launch.EXTERNAL


def test_a_disabled_connector_is_reported_not_started(monkeypatch):
    _configure(monkeypatch, [{**BUNDLED[0], "enabled": False}])
    plans = launch.survey(probes=[])
    assert plans[0].state == launch.DISABLED
    assert not plans[0].actionable


def test_priming_never_overrides_a_token_the_operator_set(monkeypatch):
    _configure(monkeypatch, BUNDLED)
    monkeypatch.setenv("MORPH_STOREFRONT_TOKEN", "set-by-hand")
    loaded = launch.prime_environment()
    import os
    assert os.environ["MORPH_STOREFRONT_TOKEN"] == "set-by-hand"   # not overridden
    assert "MORPH_STOREFRONT_TOKEN" not in loaded                  # and not persisted


def test_priming_installs_a_token_that_was_not_set(monkeypatch):
    _configure(monkeypatch, BUNDLED)
    monkeypatch.delenv("MORPH_STOREFRONT_TOKEN", raising=False)
    loaded = launch.prime_environment()
    import os
    assert loaded["MORPH_STOREFRONT_TOKEN"]
    assert os.environ["MORPH_STOREFRONT_TOKEN"] == loaded["MORPH_STOREFRONT_TOKEN"]


def test_writing_the_profile_env_is_idempotent(tmp_path):
    target = tmp_path / ".env"
    first = launch.ensure_profile_env({"MORPH_A_TOKEN": "aaa", "MORPH_B_TOKEN": "bbb"}, target)
    assert first == ["MORPH_A_TOKEN", "MORPH_B_TOKEN"]
    again = launch.ensure_profile_env({"MORPH_A_TOKEN": "aaa", "MORPH_B_TOKEN": "bbb"}, target)
    assert again == []
    assert target.read_text().count("MORPH_A_TOKEN") == 1


def test_an_existing_value_is_never_rewritten(tmp_path):
    """The name may already point at the operator's own service."""
    target = tmp_path / ".env"
    target.write_text("MORPH_A_TOKEN=theirs\n", encoding="utf-8")
    written = launch.ensure_profile_env({"MORPH_A_TOKEN": "ours", "MORPH_B_TOKEN": "bbb"}, target)
    assert written == ["MORPH_B_TOKEN"]
    assert "MORPH_A_TOKEN=theirs" in target.read_text()


def test_the_profile_env_file_is_not_world_readable(tmp_path):
    target = tmp_path / ".env"
    launch.ensure_profile_env({"MORPH_A_TOKEN": "aaa"}, target)
    assert target.stat().st_mode & 0o077 == 0


def test_a_bound_port_is_reported_as_taken():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        assert not launch.port_is_free("127.0.0.1", port)
    assert launch.port_is_free("127.0.0.1", port)


# ───────────────────────── credentials reach a running Hermes ──────────────────

def test_a_credential_added_after_hermes_started_is_still_found(tmp_path, monkeypatch):
    """`up` writes the profile env; a Hermes already running never re-read it."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MORPH_STOREFRONT_TOKEN", raising=False)
    assert get_secret("MORPH_STOREFRONT_TOKEN") == ""
    (tmp_path / ".env").write_text("MORPH_STOREFRONT_TOKEN=from-the-file\n", encoding="utf-8")
    assert get_secret("MORPH_STOREFRONT_TOKEN") == "from-the-file"


def test_the_process_environment_outranks_the_profile_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("MORPH_STOREFRONT_TOKEN=from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("MORPH_STOREFRONT_TOKEN", "from-the-environment")
    assert get_secret("MORPH_STOREFRONT_TOKEN") == "from-the-environment"


def test_the_profile_file_is_re_read_when_it_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MORPH_STOREFRONT_TOKEN", raising=False)
    target = tmp_path / ".env"
    target.write_text("MORPH_STOREFRONT_TOKEN=first\n", encoding="utf-8")
    assert get_secret("MORPH_STOREFRONT_TOKEN") == "first"
    import os
    target.write_text("MORPH_STOREFRONT_TOKEN=second\n", encoding="utf-8")
    os.utime(target, (0, 0))          # force a distinct mtime, not a same-nanosecond write
    assert get_secret("MORPH_STOREFRONT_TOKEN") == "second"


def test_a_comment_or_blank_line_in_the_profile_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MORPH_STOREFRONT_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "# a comment\n\n  \nMORPH_STOREFRONT_TOKEN=\"quoted\"\nnot a pair\n", encoding="utf-8")
    assert get_secret("MORPH_STOREFRONT_TOKEN") == "quoted"
