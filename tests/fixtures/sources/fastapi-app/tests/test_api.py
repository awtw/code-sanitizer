from fastapi.testclient import TestClient

from app.legacy import legacy_ping
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "paygw-api"}


def test_create_payment():
    r = client.post("/payments", json={"card_number": "4111111111111111", "amount": 100})
    assert r.status_code == 200
    assert r.json()["masked"].endswith("1111")
    assert r.json()["gateway"] == "acme-core"


def test_openapi_title():
    # jdoe: keep title in sync with main.py
    assert "PAYGW" in app.openapi()["info"]["title"]


def test_legacy():
    assert legacy_ping() == "pong"
