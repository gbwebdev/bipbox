"""Health endpoints.

The split between /health and /health/ready is the thing worth protecting: if
liveness started touching the database, "the process is dead" and "storage is
broken" would become indistinguishable from outside, which is precisely what
the two endpoints exist to separate (architecture.md §13).
"""

import pytest
from app.config import Settings
from app.main import RESERVED_SLUGS, create_app, sqlite_path
from fastapi.testclient import TestClient

TOKEN = "monitor-token-for-tests"


def build(**overrides):
    settings = Settings(
        _env_file=None,
        environment="development",
        database_url="sqlite+aiosqlite:///:memory:",
        image_dir="/tmp/bipbox-test-images",
        **overrides,
    )
    return create_app(settings)


@pytest.fixture
def client():
    with TestClient(build(monitor_token=TOKEN)) as c:
        yield c


# ── Liveness ─────────────────────────────────────────────────────────────────


def test_health_is_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_reports_uptime(client):
    assert client.get("/health").json()["uptime_s"] >= 0


def test_health_does_not_touch_the_database(client, monkeypatch):
    """Liveness must survive a dead database, or it cannot distinguish faults."""
    import app.db as db_module

    async def explode():
        raise AssertionError("/health must not query the database")

    monkeypatch.setattr(db_module, "check_connection", explode)

    assert client.get("/health").status_code == 200


# ── Readiness ────────────────────────────────────────────────────────────────


def test_ready_is_ok_with_a_working_database(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["checks"]["database"] is True


def test_ready_is_503_when_the_database_is_down(client, monkeypatch):
    import app.db as db_module

    async def down():
        return False

    monkeypatch.setattr(db_module, "check_connection", down)

    r = client.get("/health/ready")

    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert r.json()["checks"]["database"] is False


# ── Detail ───────────────────────────────────────────────────────────────────


def test_detail_needs_the_token(client):
    assert client.get("/health/detail").status_code == 403


def test_detail_rejects_a_wrong_token(client):
    assert client.get("/health/detail", params={"token": "nope"}).status_code == 403


def test_detail_returns_the_tree_with_the_token(client):
    r = client.get("/health/detail", params={"token": TOKEN})

    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["database"]["ok"] is True
    assert body["protocol"]["version"] == 1
    assert body["media"]["browser_voip_mode"] == "wireguard"


def test_detail_is_404_when_monitoring_is_not_configured():
    """Absent, not open: an unconfigured token must not mean no token."""
    with TestClient(build(monitor_token="")) as c:
        assert c.get("/health/detail", params={"token": ""}).status_code == 404


def test_detail_with_an_empty_token_is_not_authorised_by_accident():
    with TestClient(build(monitor_token=TOKEN)) as c:
        assert c.get("/health/detail", params={"token": ""}).status_code == 403


# ── Routing and wiring ───────────────────────────────────────────────────────


def test_there_is_no_catch_all_route(client):
    """telex returned its send page for any unmatched path, which would eat
    /{channel} and /admin (architecture.md F4.2)."""
    assert client.get("/definitely-not-a-route").status_code == 404


def test_reserved_slugs_cover_the_real_routes():
    for slug in ("admin", "api", "static", "health"):
        assert slug in RESERVED_SLUGS


def test_interactive_docs_are_not_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite+aiosqlite:///./data/bipbox.db", "data/bipbox.db"),
        ("sqlite+aiosqlite:///:memory:", None),
        ("postgresql+asyncpg://user@host/db", None),
    ],
)
def test_sqlite_path_extraction(url, expected):
    result = sqlite_path(url)
    assert (None if result is None else str(result)) == expected
