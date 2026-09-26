"""One place that says how long we wait for somebody else's server.

Connecting and waiting for an answer are not the same thing, and giving them one number is how a
*dead* host comes to be indistinguishable from a *slow* one. A TCP connect to a machine that is up
takes milliseconds; five seconds of it means nothing is listening. Reading a two-hundred-megabyte
backup or a model's completion legitimately takes minutes. Passing `timeout=300.0` to httpx sets
connect, read, write and pool to 300 alike — so a host that has vanished costs five minutes to
discover, once per attempt, on whatever thread asked.

Every outbound call in this application goes through `timeout()` so that the read budget can be as
long as the work honestly needs while the connect budget stays short.
"""

from __future__ import annotations

import httpx

#: Long enough for a congested network and a TLS handshake across a continent, short enough that a
#: host which is simply gone is known to be gone while someone is still looking at the screen.
CONNECT_SECONDS = 5.0


def timeout(read: float, *, connect: float = CONNECT_SECONDS, write: float | None = None) -> httpx.Timeout:
    """The budget for one call: `read` for the answer, a short fixed one for reaching the host.

    `write` follows `read` unless it is given, which matters for uploads: sending a large object is
    a write that takes as long as the object is big.
    """
    return httpx.Timeout(read, connect=connect, write=write if write is not None else read, pool=connect)
