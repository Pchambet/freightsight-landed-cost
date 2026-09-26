"""The worker process: `python -m app.worker`.

Same image and same environment as the API — it needs DATABASE_URL and nothing else that the API does
not already need. Concurrency and the queues to watch are environment-tunable so the Railway service
can be adjusted without a deploy.
"""

from __future__ import annotations

import logging
import os

from app.core.observability import configure_logging, init_sentry
from app.core.settings import Settings, get_settings
from app.jobs.app import get_app


def _warn_if_mute(settings: Settings) -> None:
    """In production, say loudly that this worker cannot send an e-mail — and keep working.

    Without `RESEND_API_KEY` and `ALERTS_FROM_EMAIL` the notifier is `LogNotifier`: the digest goes
    to the logs and nobody receives anything, on the one promise the product is sold on. That
    deserves an error in Sentry at every start, not a refusal to start: the same process also beats
    the heartbeat, polls the carriers, recomputes the demurrage risk, reads the invoices and syncs
    the ERP, and a missing mail key must not take all of that down with it.
    """
    if settings.is_prod and not (settings.resend_api_key and settings.alerts_from_email):
        logging.getLogger("freightsight.worker").error(
            "RESEND_API_KEY or ALERTS_FROM_EMAIL is not set: alerts are raised in the application "
            "but no e-mail leaves this worker"
        )


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    init_sentry(settings)  # the worker is where the silent failures live: it has no user watching
    _warn_if_mute(settings)
    queues = [q.strip() for q in os.environ.get("WORKER_QUEUES", "").split(",") if q.strip()]
    concurrency = int(os.environ.get("WORKER_CONCURRENCY", "2"))
    logging.getLogger("freightsight.worker").info(
        "starting worker env=%s queues=%s concurrency=%s",
        settings.app_env,
        queues or "all",
        concurrency,
    )
    get_app().run_worker(queues=queues or None, concurrency=concurrency, install_signal_handlers=True)


if __name__ == "__main__":
    main()
