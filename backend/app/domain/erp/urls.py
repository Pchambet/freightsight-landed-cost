"""Deep links into a customer's Odoo web client."""

from urllib.parse import quote

from app.core.settings import Settings, get_settings

#: The hostname the API uses to reach the demo Odoo from inside its container.
DOCKER_HOST = "host.docker.internal"


def browser_base_url(url: str, settings: Settings | None = None) -> str:
    """URL the user's browser can open.

    The API often reaches Odoo via ``host.docker.internal`` while the person demos on
    ``localhost`` — same port, different hostname. Outside the demo this rewrite has no business
    running: a real customer whose ERP is behind a host of that name would be handed a link to their
    own machine, so in production the URL is what they gave us and nothing else.
    """
    base = url.rstrip("/")
    if (settings or get_settings()).is_prod:
        return base
    return base.replace(DOCKER_HOST, "localhost")


def odoo_record_url(url: str, database: str, model: str, record_id: int) -> str:
    """Open one Odoo record in the classic web client."""
    base = browser_base_url(url)
    db = quote(database, safe="")
    m = quote(model, safe="")
    return f"{base}/web?db={db}#id={record_id}&model={m}"
