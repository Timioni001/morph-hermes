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


def test_a_read_only_connector_refuses_every_write(live_apps, morph_home, monkeypatch):
    """Read-only is a property of the connection, not of the person: no role, and no
    configuration of a role, can write through it."""
    import json

    from morph import engine, registry
    from morph.actions import Principal
    from morph.errors import PermissionDenied

    config = live_apps.config()
    for connector in config["connectors"]:
        if connector["id"] == "storefront":
            connector["read_only"] = True
    for name, token in live_apps.tokens.items():
        monkeypatch.setenv(f"MORPH_{name.upper()}_TOKEN", token)
    monkeypatch.setenv("MORPH_CONFIG", json.dumps(config))
    from morph.config import settings
    settings.cache_clear()
    registry.reset()
    engine.invalidate()

    operator = Principal.operator("ops@example.com")
    with pytest.raises(PermissionDenied) as caught:
        engine.propose(operator, source="storefront", entity="orders", record_id="ORD-2041",
                       operation="flag_for_review", params={"reason": "x"})
    assert "read-only" in caught.value.message

    # The UI is told up front, rather than letting someone click and fail.
    verdicts = {v["ref"]: v for v in engine.discover(operator)["permissions"]}
    assert verdicts["storefront.orders.flag_for_review"]["allowed"] is False
    # Another application on the same deployment is unaffected.
    assert verdicts["helpdesk.cases.assign_case"]["allowed"] is True
    # And even reaching the connector directly is refused.
    from morph.errors import PermissionDenied as Denied
    with pytest.raises(Denied):
        registry.get("storefront").apply("orders", "ORD-2041", "flag_for_review",
                                         {"reason": "x"}, expected_version=1,
                                         idempotency_key="k", actor="test")


def test_reads_retry_but_writes_without_an_idempotency_key_do_not(morph_home):
    """A timeout on a write does not tell you whether the source applied it. MORPH
    only retries a write because it always carries an idempotency key."""
    import httpx

    from morph.config import ConnectorConfig
    from morph.connectors.http_rest import HttpRestConnector

    connector = HttpRestConnector(ConnectorConfig(
        id="flaky", kind="http-rest", label="Flaky", base_url="http://127.0.0.1:1",
        token_env="", max_retries=2, retry_backoff=0.01))
    calls: list[str] = []

    class Flaky:
        def request(self, method, url, **kwargs):
            calls.append(method)
            return httpx.Response(503, request=httpx.Request(method, url))

    connector._client = Flaky()
    connector._checked_base = lambda: "http://127.0.0.1:1"

    status, _ = connector._request("GET", "/v1/things")
    assert status == 503
    assert len(calls) == 3                     # the first try plus two retries

    calls.clear()
    connector._request("POST", "/v1/things/1/operations/x", headers={"Idempotency-Key": "k"})
    assert len(calls) == 3                     # safe to retry: the key makes it idempotent

    calls.clear()
    connector._request("POST", "/v1/things/1/operations/x")
    assert len(calls) == 1                     # no key, no retry


def test_a_rate_limited_source_is_waited_out_not_hammered(morph_home):
    import httpx

    from morph.config import ConnectorConfig
    from morph.connectors.http_rest import HttpRestConnector

    connector = HttpRestConnector(ConnectorConfig(
        id="busy", kind="http-rest", label="Busy", base_url="http://127.0.0.1:1",
        token_env="", max_retries=1, retry_backoff=0.01))
    seen: list[float] = []

    class Limited:
        def __init__(self):
            self.n = 0

        def request(self, method, url, **kwargs):
            self.n += 1
            if self.n == 1:
                return httpx.Response(429, headers={"Retry-After": "0.05"},
                                      request=httpx.Request(method, url))
            return httpx.Response(200, json={"items": []}, request=httpx.Request(method, url))

    connector._client = Limited()
    connector._checked_base = lambda: "http://127.0.0.1:1"
    status, payload = connector._request("GET", "/v1/things")
    assert status == 200 and payload == {"items": []}
    # A Retry-After we cannot parse must not become an unbounded sleep.
    assert connector._retry_after(httpx.Response(429, headers={"Retry-After": "9999"},
                                                 request=httpx.Request("GET", "http://x/")), 0) <= 10
