from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.conftest import auth_headers


def test_reset_seeds_ready_to_fund_contract(client: TestClient) -> None:
    seeded = client.post("/demo/reset").json()

    milestone = client.get(
        f"/milestones/{seeded['milestone_id']}", headers=auth_headers(client, "client")
    ).json()
    assert milestone["state"] == "agreed"
    assert milestone["amount"] == "150.00"
    assert "fund" in milestone["allowed_actions"]
    contracts = client.get("/contracts", headers=auth_headers(client, "client")).json()
    assert {c["status"] for c in contracts} == {"agreed", "draft"}


def test_reset_is_repeatable(client: TestClient) -> None:
    first = client.post("/demo/reset").json()
    second = client.post("/demo/reset").json()

    assert first["contract_id"] != second["contract_id"]
    contracts = client.get("/contracts", headers=auth_headers(client, "client")).json()
    assert len(contracts) == 2  # old demo data wiped, not duplicated


def test_demo_sites_are_served(client: TestClient) -> None:
    for site in ("good-site", "buggy-site", "malicious-site", "sneaky-site"):
        response = client.get(f"/demo-sites/{site}/")
        assert response.status_code == 200
        assert "Rosie" in response.text


def test_sse_stream_accepts_token_query_param(client: TestClient) -> None:
    seeded = client.post("/demo/reset").json()
    token = auth_headers(client, "client")["Authorization"].removeprefix("Bearer ")

    response = client.get(f"/milestones/{seeded['milestone_id']}/verification/stream?token={token}")

    assert response.status_code == 200
    assert response.text.startswith("data: ")


def test_render_database_url_uses_async_driver() -> None:
    settings = Settings(database_url="postgres://u:p@host:5432/db")
    assert settings.database_url == "postgresql+asyncpg://u:p@host:5432/db"
    assert (
        Settings(database_url="sqlite+aiosqlite:///x.db").database_url == "sqlite+aiosqlite:///x.db"
    )
