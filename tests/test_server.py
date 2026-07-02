import io
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient

import server
from utils import detect_current_period


@pytest.fixture
def client():
    return TestClient(server.app)


def test_health_has_security_headers(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in res.headers
    assert "Permissions-Policy" in res.headers


def test_upload_rejects_non_excel(client):
    res = client.post(
        "/api/upload",
        files={"file": ("evil.txt", io.BytesIO(b"not an excel"), "text/plain")},
    )
    assert res.status_code == 400
    assert "tidak didukung" in res.json()["detail"].lower()


def test_upload_rejects_oversized_file(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 10)
    res = client.post(
        "/api/upload",
        files={"file": ("big.xlsx", io.BytesIO(b"x" * 100), "application/octet-stream")},
    )
    assert res.status_code == 413


def test_dashboard_requires_data(client):
    res = client.get("/api/dashboard")
    assert res.status_code == 400


def test_session_cookie_is_set_and_wellformed(client):
    res = client.get("/api/status")
    assert res.status_code == 200
    cookie = res.cookies.get("session_id")
    assert cookie is not None
    assert server._UUID_RE.match(cookie)


def test_malformed_session_id_is_not_used_as_state_key(client):
    before = set(server.session_states.keys())
    res = client.get("/api/status", headers={"x-session-id": "../../etc/passwd"})
    assert res.status_code == 200
    new_keys = set(server.session_states.keys()) - before
    assert all(server._UUID_RE.match(k) for k in new_keys)


def test_detect_current_period_from_month_keys():
    pl = {"ijk_revenue": {"curr_month": 100.0, "Maret 2027": 10.0, "Februari 2027": 8.0}}
    period = detect_current_period(pl)
    assert period["label"] == "Maret 2027"
    assert period["month"] == 3
    assert period["prev_label"] == "Februari 2027"
    assert period["yoy_label"] == "Maret 2026"


def test_detect_current_period_ignores_audited_december():
    pl = {"net_profit": {"curr_month": 5.0, "Desember 2025": 12.0, "Mei 2026": 1.0}}
    period = detect_current_period(pl)
    assert period["label"] == "Mei 2026"


def test_detect_current_period_fallback_default():
    period = detect_current_period({}, {})
    assert period["label"] == "Mei 2026"
    assert period["month"] == 5
