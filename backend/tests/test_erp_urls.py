import os
from collections.abc import Iterator

import pytest

from app.core.settings import get_settings
from app.domain.erp.urls import browser_base_url, odoo_record_url


@pytest.fixture(autouse=True)
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """These links now depend on the environment, and settings are read from it — which a test that
    never touches the database has not had set for it by the harness."""
    monkeypatch.setenv("DATABASE_URL", os.environ.get("DATABASE_URL", "postgresql+psycopg://x@x/x"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def in_prod(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", os.environ.get("DATABASE_URL", "postgresql+psycopg://x@x/x"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_browser_base_url_rewrites_docker_host_for_local_demo() -> None:
    assert browser_base_url("http://host.docker.internal:8169/") == "http://localhost:8169"


def test_odoo_record_url_points_at_the_web_client() -> None:
    assert (
        odoo_record_url("http://host.docker.internal:8169", "fstest", "stock.landed.cost", 19)
        == "http://localhost:8169/web?db=fstest#id=19&model=stock.landed.cost"
    )


def test_in_production_the_customer_url_is_left_alone(in_prod: None) -> None:
    """The rewrite exists for one laptop and one demo. A customer whose ERP really is behind a host
    of that name would be handed a link to their own machine, which is a dead link in their ERP
    card — the place we ask them to click to check our work."""
    assert browser_base_url("http://host.docker.internal:8169/") == "http://host.docker.internal:8169"
    assert (
        odoo_record_url("https://erp.customer.example", "prod", "stock.landed.cost", 4)
        == "https://erp.customer.example/web?db=prod#id=4&model=stock.landed.cost"
    )
