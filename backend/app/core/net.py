"""Where the backend is allowed to open an outbound connection to.

A customer types the URL of their own Odoo into a form, and this process then connects to it. On a
shared host that form doubles as a port scanner of the private network: `http://169.254.169.254`,
`http://postgreseu.railway.internal:5432`, our own API on its internal name. The answers
discriminate — a closed port is a socket error, an open one that does not speak XML-RPC is a
protocol error — so the address has to be vetted *before* the socket is opened, not after.

Two decisions worth writing down:

  * **Resolution, not string matching.** `http://internal.customer.tld` can resolve to 10.0.0.1 just
    as well as `http://10.0.0.1` does, and a name that answers publicly today can answer 127.0.0.1
    on the next lookup (DNS rebinding). So every call site vets again rather than trusting what was
    saved; `xmlrpc`'s transport resolves on its own and gives us no address to pin.
  * **Outside production the check stands down.** The demo reaches Odoo at
    `host.docker.internal:8169`, which is exactly the shape this refuses, and a laptop has no
    private network to protect. `assert_public_host` is therefore a no-op when `is_prod` is false,
    and `vet_public_host` is the same function with that escape hatch removed — which is what the
    tests exercise.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Sequence
from urllib.parse import urlsplit

from app.core.errors import DomainError
from app.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)

#: The ports a customer's ERP actually listens on: http, https, and Odoo's own two (`8069` for
#: xmlrpc, `8071` for the long-polling/xmlrpcs port). 22, 5432 or 6379 are questions about our
#: network, not about their ERP, so the allow-list is the check rather than a deny-list.
ALLOWED_PORTS = (80, 443, 8069, 8071)

#: Names that only ever mean "inside this infrastructure", whatever they resolve to today.
PRIVATE_SUFFIXES = (".internal", ".local", ".localhost")

DEFAULT_PORTS = {"http": 80, "https": 443}


class BlockedHost(DomainError):
    """A URL we refuse to connect to, with the reason as a machine value.

    One code and a `reason` parameter rather than one code per refusal: the screen says the same
    sentence for all of them ("this address is not reachable from the internet"), and the reason is
    what a support ticket needs to explain *which* rule fired.
    """

    status = 422
    code = "URL_NOT_PUBLIC"


def _blocked(host: str, reason: str) -> BlockedHost:
    return BlockedHost(
        f"{host} is not a publicly reachable address, so this server will not connect to it.",
        params={"host": host, "reason": reason},
    )


def _address_reason(raw: str) -> str | None:
    """Why this IP is out of bounds, or None if it is an ordinary public address."""
    address = ipaddress.ip_address(raw)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        # `::ffff:127.0.0.1` is loopback wearing a v6 hat, and `is_loopback` says False on it.
        address = address.ipv4_mapped
    if address.is_unspecified:
        return "unspecified"
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link_local"
    if address.is_multicast:
        return "multicast"
    # Reserved before private: 240.0.0.0/4 is both, and "reserved" is the word that describes it.
    if address.is_reserved:
        return "reserved"
    if address.is_private:
        return "private"
    return None


def vet_public_host(url: str, *, extra_ports: Sequence[int] = ()) -> list[str]:
    """Refuse `url` unless it resolves to public addresses on an allowed port.

    Returns the addresses it resolved to, so a caller with a transport that can connect to an
    address rather than a name has something to pin. Raises `BlockedHost` otherwise.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").strip().rstrip(".").lower()
    shown = host or url
    if parts.scheme not in DEFAULT_PORTS:
        raise _blocked(shown, "scheme")
    if not host:
        raise _blocked(url, "no_host")
    try:
        port = parts.port or DEFAULT_PORTS[parts.scheme]
    except ValueError as exc:  # a port that is not a number at all
        raise _blocked(shown, "port") from exc
    if port not in ALLOWED_PORTS and port not in tuple(extra_ports):
        raise _blocked(shown, "port")
    if host == "localhost" or host.endswith(PRIVATE_SUFFIXES):
        raise _blocked(shown, "internal_name")

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        # A name nobody can resolve is not an attack, but it is not a connection either, and
        # refusing here is the only place that can say so without a 60-second timeout.
        raise _blocked(shown, "unresolvable") from exc
    addresses = [str(info[4][0]) for info in infos]
    if not addresses:  # pragma: no cover - getaddrinfo either raises or answers
        raise _blocked(shown, "unresolvable")
    for address in addresses:
        reason = _address_reason(address)
        if reason is not None:
            raise _blocked(shown, reason)
    return addresses


def assert_public_host(url: str, settings: Settings | None = None) -> list[str]:
    """The same check, skipped only where someone said so: local development against a private
    Odoo needs `ALLOW_PRIVATE_OUTBOUND=true` AND a non-prod `APP_ENV`. Either one missing, and the
    URL is vetted."""
    conf = settings or get_settings()
    if conf.allow_private_outbound and not conf.is_prod:
        return []
    return vet_public_host(url, extra_ports=conf.outbound_extra_ports)
