"""Connect-time SSRF guard for MORPH's own HTTP clients.

Validating a URL before a request leaves a window: DNS can answer differently
between the check and the socket connect, which is how a public hostname reaches a
private address.  Closing it means validating the address the socket actually dials.

Hermes has a guarded client of its own, but it is not part of the published plugin
API (catalog rule 9: public surfaces only), and it blocks loopback — which MORPH
deliberately allows for the bundled demo applications.  So MORPH installs an
equivalent guard on its own httpx client: the hostname is resolved here, every
answer is checked against the policy, and the connection is dialled by the validated
IP while the original hostname is kept for the ``Host`` header, TLS SNI and
certificate verification.

If httpx's internals move, :func:`install` reports that it could not attach and the
connector falls back to per-request URL validation alone.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any

from morph.errors import ConnectorError


def _allowed_addresses(host: str, *, allow_loopback: bool) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConnectorError(f"{host!r} does not resolve") from exc
    allowed: list[str] = []
    for info in infos:
        raw = info[4][0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if address.is_loopback:
            if allow_loopback:
                allowed.append(raw)
            continue
        if (address.is_private or address.is_link_local or address.is_reserved
                or address.is_multicast or address.is_unspecified):
            continue
        allowed.append(raw)
    if not allowed:
        raise ConnectorError(
            f"every address {host!r} resolves to is blocked by MORPH's network policy")
    return allowed


def install(client: Any, *, allow_loopback: bool) -> bool:
    """Attach the guard to an ``httpx.Client``. Returns whether it attached."""
    try:
        import httpcore  # noqa: F401  (imported for the base class below)
        transport = client._transport  # noqa: SLF001 - httpx has no public seam for this
        pool = transport._pool  # noqa: SLF001
        backend = pool._network_backend  # noqa: SLF001
    except Exception:  # noqa: BLE001 - any shape we do not recognise means no guard
        return False

    class _GuardedBackend:
        """Delegates to httpx's own backend, dialling only validated addresses."""

        def __getattr__(self, name: str) -> Any:
            return getattr(backend, name)

        def connect_tcp(self, host: str, port: int, timeout: Any = None,
                        local_address: Any = None, socket_options: Any = None) -> Any:
            last: Exception | None = None
            for address in _allowed_addresses(host, allow_loopback=allow_loopback):
                try:
                    return backend.connect_tcp(address, port, timeout=timeout,
                                               local_address=local_address,
                                               socket_options=socket_options)
                except OSError as exc:
                    last = exc
            raise last or ConnectorError(f"could not connect to {host!r}")

        def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectorError("MORPH connectors do not use unix sockets")

    try:
        pool._network_backend = _GuardedBackend()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return False
    return True
