"""What the backend is allowed to connect to when a customer supplies the address.

The form that takes an Odoo URL connects to it on demand, as often as someone asks. Without this
check it answers questions about our own private network — which port is open on
`postgreseu.railway.internal`, what our API says on its internal name — and the answers differ
enough to be read.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.net import BlockedHost, assert_public_host, vet_public_host
from app.core.settings import get_settings

#: A real public address, written as a literal so the test never depends on a DNS lookup.
PUBLIC = "93.184.216.34"


def reason_for(url: str, **kwargs: object) -> str:
    with pytest.raises(BlockedHost) as raised:
        vet_public_host(url, **kwargs)  # type: ignore[arg-type]
    params = raised.value.extra["params"]
    assert isinstance(params, dict)
    return str(params["reason"])


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://127.0.0.1:8069", "loopback"),
        ("http://[::1]:8069", "loopback"),
        # Loopback wearing a v6 hat: `is_loopback` says False on it, which is the whole trap.
        ("http://[::ffff:127.0.0.1]:8069", "loopback"),
        ("http://10.0.0.5:8069", "private"),
        ("http://192.168.1.10", "private"),
        ("http://[fd00::1]:8069", "private"),
        # The cloud metadata address, which is the first thing anyone tries.
        ("http://169.254.169.254", "link_local"),
        ("http://[fe80::1]:8069", "link_local"),
        ("http://0.0.0.0:8069", "unspecified"),
        ("http://224.0.0.1", "multicast"),
        ("http://240.0.0.1", "reserved"),
        ("http://localhost:8069", "internal_name"),
        ("http://postgreseu.railway.internal:8069", "internal_name"),
        ("http://odoo.local:8069", "internal_name"),
        ("http://host.docker.internal:8069", "internal_name"),
        # A name that resolves publicly is still refused on a port no ERP listens on.
        (f"http://{PUBLIC}:5432", "port"),
        (f"http://{PUBLIC}:22", "port"),
        (f"ftp://{PUBLIC}", "scheme"),
        ("file:///etc/passwd", "scheme"),
    ],
)
def test_each_way_of_pointing_inside_is_refused_with_its_reason(url: str, reason: str) -> None:
    assert reason_for(url) == reason


def test_a_name_that_does_not_resolve_is_refused_rather_than_dialled() -> None:
    """Not an attack, but not a connection either — and saying so here costs no timeout."""
    assert reason_for("https://zz-not-a-real-host-fs.invalid") == "unresolvable"


def test_an_ordinary_hosted_odoo_goes_through() -> None:
    assert vet_public_host(f"https://{PUBLIC}") == [PUBLIC]
    assert vet_public_host(f"http://{PUBLIC}:8069") == [PUBLIC]
    assert vet_public_host(f"http://{PUBLIC}:8071") == [PUBLIC]


def test_a_deployment_can_add_a_port_its_customer_actually_uses() -> None:
    assert reason_for(f"https://{PUBLIC}:8443") == "port"
    assert vet_public_host(f"https://{PUBLIC}:8443", extra_ports=(8443,)) == [PUBLIC]


def test_the_message_names_the_host_and_nothing_else() -> None:
    with pytest.raises(BlockedHost) as raised:
        vet_public_host("http://admin:secret@10.0.0.5:8069")
    assert "secret" not in raised.value.message
    assert raised.value.extra["params"] == {"host": "10.0.0.5", "reason": "private"}
    assert raised.value.code == "URL_NOT_PUBLIC"


# ---------------------------------------------------------------------------- where it applies


def _settings_env(monkeypatch: pytest.MonkeyPatch, app_env: str) -> Iterator[None]:
    import os

    from app.core.settings import get_settings

    monkeypatch.setenv("APP_ENV", app_env)
    # Settings are read from the environment, and a test that never touches the database has not
    # had one set for it by the harness.
    monkeypatch.setenv("DATABASE_URL", os.environ.get("DATABASE_URL", "postgresql+psycopg://x@x/x"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def in_prod(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    yield from _settings_env(monkeypatch, "prod")


@pytest.fixture
def in_dev(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    yield from _settings_env(monkeypatch, "dev")


def test_outside_production_the_local_demo_still_works(in_dev: None) -> None:
    """`host.docker.internal:8169` is the demo Odoo, and a laptop has no private network to guard."""
    assert assert_public_host("http://host.docker.internal:8169") == []


def test_a_lost_app_env_alone_does_not_reopen_the_private_network(
    in_dev: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`APP_ENV` lives in a dashboard and can be lost in a clone or a restore. Without the second,
    explicit switch the URL is vetted whatever the environment says it is."""
    monkeypatch.setenv("ALLOW_PRIVATE_OUTBOUND", "false")
    get_settings.cache_clear()
    with pytest.raises(BlockedHost):
        assert_public_host("http://host.docker.internal:8169")


def test_the_switch_does_nothing_in_production(in_prod: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_PRIVATE_OUTBOUND", "true")
    get_settings.cache_clear()
    with pytest.raises(BlockedHost):
        assert_public_host("http://host.docker.internal:8169")


def test_in_production_the_same_address_is_refused(in_prod: None) -> None:
    with pytest.raises(BlockedHost):
        assert_public_host("http://host.docker.internal:8169")
