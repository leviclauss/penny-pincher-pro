"""HTTP-level coverage for /api/positions.

Drives the state machine through the public router and verifies the response
schema, the persisted rows, and the failure modes (404 / 409 / 422).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "positions_api.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    from core.config import get_settings
    from db import session as db_session

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    from api.main import app

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def _short_put_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "AAPL",
        "expiration": "2026-06-19",
        "strike": 170.0,
        "contracts": 1,
        "credit": 2.50,
        "opened_on": "2026-05-01",
    }
    payload.update(overrides)
    return payload


def test_open_short_put_creates_position(client: TestClient) -> None:
    resp = client.post("/api/positions/short-put", json=_short_put_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["state"] == "short_put"
    assert body["closed_at"] is None
    assert len(body["legs"]) == 1
    leg = body["legs"][0]
    assert leg["leg_type"] == "short_put"
    assert leg["entry_price"] == 2.50
    assert leg["outcome"] == "open"


def test_invalid_input_returns_422(client: TestClient) -> None:
    bad = _short_put_payload(contracts=0)
    resp = client.post("/api/positions/short-put", json=bad)
    assert resp.status_code == 422


def test_close_put_endpoint_realizes_pnl(client: TestClient) -> None:
    create = client.post("/api/positions/short-put", json=_short_put_payload(credit=3.0)).json()
    pid = create["id"]

    resp = client.post(
        f"/api/positions/{pid}/close-put",
        json={"debit": 1.20, "closed_on": "2026-05-15", "fees": 1.30},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "closed"
    leg = body["legs"][0]
    assert leg["outcome"] == "closed"
    assert leg["realized_pnl"] == pytest.approx(178.70)


def test_assign_then_covered_call_then_called_away(client: TestClient) -> None:
    pid = client.post("/api/positions/short-put", json=_short_put_payload()).json()["id"]

    assign = client.post(f"/api/positions/{pid}/assign-put", json={"assigned_on": "2026-06-19"})
    assert assign.status_code == 200
    assert assign.json()["state"] == "long_shares"

    cc = client.post(
        f"/api/positions/{pid}/covered-call",
        json={
            "expiration": "2026-07-17",
            "strike": 175.0,
            "contracts": 1,
            "credit": 1.80,
            "opened_on": "2026-06-20",
        },
    )
    assert cc.status_code == 200
    assert cc.json()["state"] == "covered_call"

    away = client.post(f"/api/positions/{pid}/called-away", json={"called_on": "2026-07-17"})
    assert away.status_code == 200
    body = away.json()
    assert body["state"] == "closed"
    legs_by_type = {leg["leg_type"]: leg for leg in body["legs"]}
    assert legs_by_type["covered_call"]["realized_pnl"] == pytest.approx(180.0)
    assert legs_by_type["shares"]["realized_pnl"] == pytest.approx(500.0)


def test_invalid_transition_returns_409(client: TestClient) -> None:
    pid = client.post("/api/positions/short-put", json=_short_put_payload()).json()["id"]
    # Cannot open a covered call from short_put state
    resp = client.post(
        f"/api/positions/{pid}/covered-call",
        json={
            "expiration": "2026-07-17",
            "strike": 175.0,
            "contracts": 1,
            "credit": 1.80,
            "opened_on": "2026-06-20",
        },
    )
    assert resp.status_code == 409


def test_get_unknown_returns_404(client: TestClient) -> None:
    resp = client.get("/api/positions/9999")
    assert resp.status_code == 404


def test_list_filters_by_state(client: TestClient) -> None:
    pid_open = client.post("/api/positions/short-put", json=_short_put_payload()).json()["id"]
    pid_other = client.post(
        "/api/positions/short-put", json=_short_put_payload(symbol="MSFT")
    ).json()["id"]
    client.post(f"/api/positions/{pid_other}/expire-put", json={"expired_on": "2026-06-19"})

    open_only = client.get("/api/positions?state=short_put").json()
    closed_only = client.get("/api/positions?state=closed").json()

    assert {p["id"] for p in open_only} == {pid_open}
    assert {p["id"] for p in closed_only} == {pid_other}


def test_list_filters_by_symbol(client: TestClient) -> None:
    client.post("/api/positions/short-put", json=_short_put_payload(symbol="AAPL"))
    client.post("/api/positions/short-put", json=_short_put_payload(symbol="MSFT"))

    aapl = client.get("/api/positions?symbol=aapl").json()
    assert len(aapl) == 1
    assert aapl[0]["symbol"] == "AAPL"


def test_patch_updates_notes(client: TestClient) -> None:
    pid = client.post("/api/positions/short-put", json=_short_put_payload()).json()["id"]
    resp = client.patch(f"/api/positions/{pid}", json={"notes": "watching earnings"})
    assert resp.status_code == 200
    assert resp.json()["notes"] == "watching earnings"


def test_open_long_shares_endpoint(client: TestClient) -> None:
    resp = client.post(
        "/api/positions/long-shares",
        json={
            "symbol": "msft",
            "shares": 200,
            "cost_basis": 410.50,
            "opened_on": "2026-04-15",
            "acquisition_source": "open_market",
            "fees": 1.25,
            "notes": "bought during dip",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["symbol"] == "MSFT"
    assert body["state"] == "long_shares"
    assert body["acquisition_source"] == "open_market"
    assert body["notes"] == "bought during dip"
    assert len(body["legs"]) == 1
    leg = body["legs"][0]
    assert leg["leg_type"] == "shares"
    assert leg["shares"] == 200
    assert leg["entry_price"] == pytest.approx(410.50)
    assert leg["fees"] == pytest.approx(1.25)


def test_open_long_shares_rejects_unknown_acquisition_source(client: TestClient) -> None:
    resp = client.post(
        "/api/positions/long-shares",
        json={
            "symbol": "AAPL",
            "shares": 100,
            "cost_basis": 170.0,
            "opened_on": "2026-05-01",
            "acquisition_source": "inheritance",
        },
    )
    assert resp.status_code == 422


def test_open_covered_call_fresh_endpoint(client: TestClient) -> None:
    resp = client.post(
        "/api/positions/covered-call",
        json={
            "symbol": "aapl",
            "shares": 200,
            "cost_basis": 170.0,
            "opened_on": "2026-05-01",
            "acquisition_source": "assignment",
            "expiration": "2026-06-19",
            "strike": 180.0,
            "contracts": 2,
            "credit": 2.40,
            "fees": 0.65,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["state"] == "covered_call"
    assert body["acquisition_source"] == "assignment"
    legs_by_type = {leg["leg_type"]: leg for leg in body["legs"]}
    assert legs_by_type["shares"]["shares"] == 200
    assert legs_by_type["shares"]["entry_price"] == pytest.approx(170.0)
    assert legs_by_type["covered_call"]["contracts"] == 2
    assert legs_by_type["covered_call"]["strike"] == pytest.approx(180.0)
    assert legs_by_type["covered_call"]["entry_price"] == pytest.approx(2.40)


def test_open_covered_call_fresh_rejects_undercovered(client: TestClient) -> None:
    resp = client.post(
        "/api/positions/covered-call",
        json={
            "symbol": "AAPL",
            "shares": 100,
            "cost_basis": 170.0,
            "opened_on": "2026-05-01",
            "acquisition_source": "open_market",
            "expiration": "2026-06-19",
            "strike": 180.0,
            "contracts": 2,
            "credit": 2.40,
        },
    )
    assert resp.status_code == 422


def test_close_shares_manual_endpoint(client: TestClient) -> None:
    pid = client.post("/api/positions/short-put", json=_short_put_payload()).json()["id"]
    client.post(f"/api/positions/{pid}/assign-put", json={"assigned_on": "2026-06-19"})
    resp = client.post(
        f"/api/positions/{pid}/close-shares",
        json={"sale_price": 172.0, "closed_on": "2026-07-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "closed"
    shares = next(leg for leg in body["legs"] if leg["leg_type"] == "shares")
    assert shares["realized_pnl"] == pytest.approx(200.0)


def _open_and_close_put(client: TestClient) -> int:
    pid = client.post("/api/positions/short-put", json=_short_put_payload(credit=3.0)).json()["id"]
    client.post(
        f"/api/positions/{pid}/close-put",
        json={"debit": 1.20, "closed_on": "2026-05-15", "fees": 1.30},
    )
    return pid


def test_delete_closed_position(client: TestClient) -> None:
    pid = _open_and_close_put(client)
    resp = client.delete(f"/api/positions/{pid}")
    assert resp.status_code == 204, resp.text
    assert client.get(f"/api/positions/{pid}").status_code == 404


def test_delete_open_position_rejected(client: TestClient) -> None:
    pid = client.post("/api/positions/short-put", json=_short_put_payload()).json()["id"]
    resp = client.delete(f"/api/positions/{pid}")
    assert resp.status_code == 409
    # still exists
    assert client.get(f"/api/positions/{pid}").status_code == 200


def test_delete_unknown_position_404(client: TestClient) -> None:
    assert client.delete("/api/positions/9999").status_code == 404


def test_patch_leg_on_closed_position(client: TestClient) -> None:
    pid = _open_and_close_put(client)
    leg_id = client.get(f"/api/positions/{pid}").json()["legs"][0]["id"]

    resp = client.patch(
        f"/api/positions/{pid}/legs/{leg_id}",
        json={"entry_price": 3.50, "exit_price": 0.75, "fees": 2.00, "realized_pnl": 273.0},
    )
    assert resp.status_code == 200, resp.text
    leg = next(item for item in resp.json()["legs"] if item["id"] == leg_id)
    assert leg["entry_price"] == pytest.approx(3.50)
    assert leg["exit_price"] == pytest.approx(0.75)
    assert leg["fees"] == pytest.approx(2.00)
    assert leg["realized_pnl"] == pytest.approx(273.0)


def test_patch_leg_rejected_on_open_position(client: TestClient) -> None:
    pid = client.post("/api/positions/short-put", json=_short_put_payload()).json()["id"]
    leg_id = client.get(f"/api/positions/{pid}").json()["legs"][0]["id"]
    resp = client.patch(f"/api/positions/{pid}/legs/{leg_id}", json={"entry_price": 9.99})
    assert resp.status_code == 409


def test_patch_leg_unknown_leg_404(client: TestClient) -> None:
    pid = _open_and_close_put(client)
    resp = client.patch(f"/api/positions/{pid}/legs/9999", json={"fees": 1.0})
    assert resp.status_code == 404


def test_patch_leg_negative_fees_rejected(client: TestClient) -> None:
    pid = _open_and_close_put(client)
    leg_id = client.get(f"/api/positions/{pid}").json()["legs"][0]["id"]
    resp = client.patch(f"/api/positions/{pid}/legs/{leg_id}", json={"fees": -0.5})
    assert resp.status_code == 422


def test_patch_position_dates_on_closed_position(client: TestClient) -> None:
    pid = _open_and_close_put(client)
    resp = client.patch(
        f"/api/positions/{pid}",
        json={"opened_at": "2026-05-02T00:00:00Z", "closed_at": "2026-05-20T00:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["opened_at"].startswith("2026-05-02")
    assert body["closed_at"].startswith("2026-05-20")


def test_patch_position_dates_rejected_on_open(client: TestClient) -> None:
    pid = client.post("/api/positions/short-put", json=_short_put_payload()).json()["id"]
    resp = client.patch(f"/api/positions/{pid}", json={"opened_at": "2026-05-02T00:00:00Z"})
    assert resp.status_code == 409


def test_patch_position_dates_inverted_rejected(client: TestClient) -> None:
    pid = _open_and_close_put(client)
    resp = client.patch(
        f"/api/positions/{pid}",
        json={"opened_at": "2026-06-01T00:00:00Z", "closed_at": "2026-05-15T00:00:00Z"},
    )
    assert resp.status_code == 422
