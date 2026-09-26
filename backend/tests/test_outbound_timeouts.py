"""Every call out of this application has to come back.

Not a unit test of one adapter: a standing guard over all of them. The failure these prevent is not
a wrong answer, it is a thread parked on a socket — and the worker holds queueing locks, so one
unreachable third party stops work that has nothing to do with it.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import httpx
import pytest

from app.core.http import CONNECT_SECONDS, timeout

APP = Path(__file__).resolve().parent.parent / "app"

# TEST-NET-2. It routes nowhere and *drops* packets rather than refusing them, which is how a host
# behind a changed firewall behaves — the case a connect timeout exists for.
BLACK_HOLE = "http://198.51.100.7:9"


def clients() -> dict[str, tuple[httpx.Client, float]]:
    """One built client per adapter that talks to somebody else, with the budget it declares."""
    from app.adapters.extraction import llm_extractor
    from app.adapters.notifications import resend
    from app.adapters.s3 import TIMEOUT_SECONDS as S3_SECONDS
    from app.adapters.s3 import S3Client
    from app.adapters.tracking import shipsgo, terminal49

    return {
        "resend": (
            resend.ResendNotifier(api_key="k", from_email="a@b.test")._http(),
            resend.TIMEOUT_SECONDS,
        ),
        "shipsgo": (shipsgo.ShipsgoConnector(api_key="k")._http(), shipsgo.TIMEOUT_SECONDS),
        "terminal49": (
            terminal49.Terminal49Provider(secret="s", api_key="k")._http(),
            terminal49.TIMEOUT_SECONDS,
        ),
        "s3": (S3Client("b", "fr-par", "a", "s", "https://s3.example.test")._http(), S3_SECONDS),
        "extraction": (
            httpx.Client(timeout=timeout(llm_extractor.TIMEOUT_SECONDS)),
            llm_extractor.TIMEOUT_SECONDS,
        ),
    }


def test_every_adapter_reaches_out_with_a_short_connect_budget() -> None:
    """Connecting and waiting for an answer are different questions.

    A read may honestly need minutes — a backup upload, a model reading an invoice. Reaching the
    host never does, and when the two share a number a host that is *gone* costs the slow case to
    discover.
    """
    for name, (client, _) in clients().items():
        assert client.timeout.connect == CONNECT_SECONDS, f"{name} connects without a short budget"
        assert client.timeout.read is not None, f"{name} would wait forever for an answer"
        assert client.timeout.pool == CONNECT_SECONDS, f"{name} would queue forever for a connection"


def test_each_adapter_uses_the_budget_it_declares() -> None:
    """The constant beside the adapter is the one that reaches the socket — not a literal that has
    drifted from the comment explaining it."""
    for name, (client, declared) in clients().items():
        assert client.timeout.read == declared, f"{name} does not use its own TIMEOUT_SECONDS"


def test_no_budget_is_absurd_in_either_direction() -> None:
    """Measured per call: long enough for the slowest honest case, and nowhere near forever."""
    for name, (_, declared) in clients().items():
        assert 5.0 <= declared <= 600.0, f"{name} waits {declared}s for an answer"


def test_a_host_that_drops_packets_is_given_up_on() -> None:
    """The guarantee the numbers above are supposed to buy, taken to a real socket."""
    client = httpx.Client(base_url=BLACK_HOLE, timeout=timeout(120.0, connect=2.0))
    started = time.monotonic()
    outcome: list[str] = []

    def probe() -> None:
        try:
            client.get("/anything")
        except httpx.HTTPError as exc:
            outcome.append(type(exc).__name__)

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive(), "the connect budget did not apply"
    # It gave up on the connect, not after the 120s read budget it was also given.
    assert time.monotonic() - started < 30
    assert outcome == ["ConnectTimeout"]


def test_the_jwks_fetch_does_not_use_pyjwt_s_thirty_seconds() -> None:
    """This one is on the path of every signed-in request, so it is the one that must not dawdle."""
    from app.adapters.auth_clerk import JWKS_TIMEOUT_SECONDS, _jwks_client

    _jwks_client.cache_clear()
    client = _jwks_client("https://clerk.example.test/.well-known/jwks.json")
    assert JWKS_TIMEOUT_SECONDS <= 5.0
    assert client.timeout == JWKS_TIMEOUT_SECONDS
    _jwks_client.cache_clear()


# ---------------------------------------------------------------------------- the standing guard

_BARE_CLIENT = re.compile(r"httpx\.(Client|AsyncClient|get|post|put|delete|request|stream)\(")


@pytest.mark.parametrize("path", sorted(APP.rglob("*.py")), ids=lambda p: str(p.name))
def test_no_outbound_call_is_made_without_a_timeout(path: Path) -> None:
    """The one that catches the adapter somebody writes next year.

    httpx's own default is a five-second everything, which is wrong in both directions here: too
    short for an upload, and silent about it. An explicit budget is not optional.
    """
    source = path.read_text()
    for match in _BARE_CLIENT.finditer(source):
        tail = source[match.end() : match.end() + 400]
        depth, call = 1, ""
        for character in tail:
            depth += {"(": 1, ")": -1}.get(character, 0)
            if depth == 0:
                break
            call += character
        assert "timeout" in call, (
            f"{path.name}: {match.group(0)}…) goes out without a timeout. "
            f"Use app.core.http.timeout(read) so the connect budget stays short."
        )
