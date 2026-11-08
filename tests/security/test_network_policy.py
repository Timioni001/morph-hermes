"""Network policy: what MORPH will and will not connect to."""
from __future__ import annotations

import json

import pytest

from morph import registry
from morph.config import assert_url_allowed, get_secret, settings
from morph.errors import ConfigError


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",          # cloud metadata
    "http://metadata.google.internal/computeMetadata/",  # cloud metadata by name
    "http://10.0.0.5/api",                               # private range
    "http://192.168.1.10:8080",                          # private range
    "https://user:password@example.com/api",             # embedded credentials
    "file:///etc/passwd",                                # not http(s)
    "gopher://example.com/",                             # not http(s)
    "https://example.com/api?token=x",                   # query in a base url
    "http://127.0.0.1:8811",                             # loopback without the opt-in
])
def test_refused_base_urls(url):
    with pytest.raises(ConfigError):
        assert_url_allowed(url, allow_loopback=False)


def test_loopback_needs_an_explicit_opt_in():
    with pytest.raises(ConfigError):
        assert_url_allowed("http://127.0.0.1:8811", allow_loopback=False)
    assert assert_url_allowed("http://127.0.0.1:8811", allow_loopback=True) \
        == "http://127.0.0.1:8811"


def test_an_allowlist_narrows_further():
    with pytest.raises(ConfigError) as caught:
        assert_url_allowed("http://127.0.0.1:8811", allow_loopback=True,
                           allowed_hosts=("api.example.com",))
    assert "allowlist" in caught.value.message


def test_plain_http_is_refused_for_a_remote_host():
    with pytest.raises(ConfigError) as caught:
        assert_url_allowed("http://example.com/api", allow_loopback=True)
    assert "HTTPS" in caught.value.message or "loopback" in caught.value.message


def test_a_connector_pointed_at_a_private_address_never_loads(morph_home, monkeypatch):
    monkeypatch.setenv("MORPH_CONFIG", json.dumps({
        "connectors": [{"id": "evil", "kind": "http-rest", "base_url": "http://169.254.169.254"}]}))
    from morph.config import settings as load
    load.cache_clear()
    with pytest.raises(ConfigError):
        load()


def test_the_connect_time_guard_rejects_a_private_resolution():
    """The guard validates the address the socket dials, not just the URL."""
    from morph.connectors.netguard import _allowed_addresses
    with pytest.raises(Exception):
        _allowed_addresses("localhost", allow_loopback=False)
    assert _allowed_addresses("localhost", allow_loopback=True)


def test_the_guard_is_actually_attached_to_live_clients(live_config):
    probes = {probe["id"]: probe for probe in registry.health()}
    assert all(probe["ok"] for probe in probes.values())
    assert all(probe.get("connect_guard") is True for probe in probes.values()), probes


def test_secrets_are_read_by_name_and_never_inlined(morph_home, monkeypatch):
    monkeypatch.setenv("MORPH_TEST_TOKEN", "s3cret")
    assert get_secret("MORPH_TEST_TOKEN") == "s3cret"
    with pytest.raises(ConfigError):
        get_secret("not-an-env-name")
    monkeypatch.setenv("MORPH_CONFIG", json.dumps({
        "allow_loopback": True,
        "connectors": [{"id": "x", "kind": "http-rest", "base_url": "http://127.0.0.1:9",
                        "token_env": "lowercase_name"}]}))
    from morph.config import settings as load
    load.cache_clear()
    with pytest.raises(ConfigError):
        load()


def test_a_connector_without_its_credential_fails_loudly(live_apps, morph_home, monkeypatch):
    monkeypatch.setenv("MORPH_CONFIG", json.dumps(live_apps.config(only=("storefront",))))
    monkeypatch.delenv("MORPH_STOREFRONT_TOKEN", raising=False)
    from morph.config import settings as load
    load.cache_clear()
    registry.reset()
    probe = registry.health()[0]
    assert probe["ok"] is False
    assert "credential" in probe["error"]


def test_a_source_that_answers_with_a_redirect_is_refused(morph_home, monkeypatch):
    """MORPH never follows a redirect: a redirect is how an allowed host hands a
    client to one that was never allowed."""
    import httpx

    from morph.config import ConnectorConfig
    from morph.connectors.http_rest import HttpRestConnector

    config = ConnectorConfig(id="r", kind="http-rest", label="r",
                             base_url="http://127.0.0.1:1", token_env="")
    connector = HttpRestConnector(config)

    class Redirecting:
        def request(self, *args, **kwargs):
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/"},
                                  request=httpx.Request("GET", "http://127.0.0.1:1/"))

    connector._client = Redirecting()
    monkeypatch.setattr(connector, "_checked_base", lambda: "http://127.0.0.1:1")
    with pytest.raises(Exception) as caught:
        connector.describe(refresh=True)
    assert "redirect" in str(caught.value)


def test_an_oversized_response_is_refused(morph_home, monkeypatch):
    import httpx

    from morph.config import ConnectorConfig
    from morph.connectors.http_rest import MAX_RESPONSE_BYTES, HttpRestConnector

    connector = HttpRestConnector(ConnectorConfig(id="big", kind="http-rest", label="big",
                                                  base_url="http://127.0.0.1:1", token_env=""))

    class Huge:
        def request(self, *args, **kwargs):
            return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 10),
                                  request=httpx.Request("GET", "http://127.0.0.1:1/"))

    connector._client = Huge()
    monkeypatch.setattr(connector, "_checked_base", lambda: "http://127.0.0.1:1")
    with pytest.raises(Exception) as caught:
        connector.describe(refresh=True)
    assert "bytes" in str(caught.value)
