"""12-factor configuration. Nothing secret has a default: the app refuses to boot without it."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "preview", "prod"] = "dev"
    database_url: str = Field(description="postgresql+psycopg://user:pass@host:5432/db")
    cors_origins: list[str] = ["http://localhost:3000"]
    default_base_currency: str = "EUR"
    #: Accept the unauthenticated `X-Org-Id` header as a full OWNER principal. Off unless someone
    #: turns it on, and never in prod: `APP_ENV` alone guarded this, and `APP_ENV` lives in a
    #: dashboard where it can be lost in a clone or a restore. Two switches, one of them versioned.
    allow_dev_principal: bool = False
    #: Ports an outbound connection may use on top of `core.net.ALLOWED_PORTS` — an escape hatch for
    #: the customer whose Odoo sits behind a reverse proxy on an unusual port, set per deployment.
    outbound_extra_ports: list[int] = []
    #: Let a customer-supplied URL point at a private address (the throw-away Odoo on
    #: host.docker.internal). Same two-switch rule as the development principal, for the same
    #: reason: with `APP_ENV` alone, a lost variable would quietly turn the ERP form back into a
    #: scanner of the private network.
    allow_private_outbound: bool = False

    # Clerk (Phase 1 auth). When unset outside prod, the X-Org-Id dev header is accepted instead.
    clerk_jwks_url: str | None = None
    clerk_issuer: str | None = None
    clerk_audience: str | None = None
    # Clerk session tokens carry `azp` (the origin that requested them). When set, only these are accepted.
    clerk_authorized_parties: list[str] = []

    # FX rates: Frankfurter (ECB reference rates), no key needed.
    fx_api_url: str = "https://api.frankfurter.dev/v1"

    # Container tracking. The signing secret alone is enough to receive and verify webhooks; the API
    # key is only needed to subscribe or poll, so a deployment can run webhook-only.
    tracking_webhook_secret_terminal49: str | None = None
    terminal49_api_key: str | None = None
    terminal49_base_url: str = "https://api.terminal49.com/v2"
    tracking_webhook_max_age_seconds: int = 900
    shipsgo_api_key: str | None = None
    tracking_webhook_secret_shipsgo: str | None = None

    # Alert e-mails. Without both of these the application logs its digests instead of sending them.
    resend_api_key: str | None = None
    alerts_from_email: str | None = None
    resend_base_url: str = "https://api.resend.com"

    # ERP connectors. The key that seals customers' ERP credentials at rest.
    erp_encryption_key: str | None = None
    #: Ceiling on any single call to a customer's ERP. Without one, an ERP that drops packets holds
    #: the daily sweep open forever — and the sweep takes a queueing lock, so every other customer
    #: stops with it.
    erp_timeout_seconds: float = 60.0

    # Observability. Without a DSN the application logs exactly the same and reports nothing.
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.1

    # Invoice extraction. Without a key, invoices are read by the pattern extractor only.
    extraction_api_key: str | None = None
    extraction_model: str = "claude-opus-5"
    extraction_api_url: str = "https://api.anthropic.com/v1/messages"

    # Document storage. Without a complete S3 configuration, uploads are kept in Postgres.
    documents_s3_bucket: str | None = None
    documents_s3_region: str = "fr-par"
    documents_s3_endpoint: str = "https://s3.fr-par.scw.cloud"
    documents_s3_access_key: str | None = None
    documents_s3_secret_key: str | None = None

    @property
    def s3_documents_enabled(self) -> bool:
        return bool(
            self.documents_s3_bucket and self.documents_s3_access_key and self.documents_s3_secret_key
        )

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def clerk_enabled(self) -> bool:
        return bool(self.clerk_jwks_url and self.clerk_issuer)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # database_url comes from the environment
