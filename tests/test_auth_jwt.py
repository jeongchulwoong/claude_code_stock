"""tests/test_auth_jwt.py — JWT 발급/검증 + dashboard /api/auth 회귀."""

from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.auth_jwt import (   # noqa: E402
    TTL_ADMIN_SECONDS,
    TTL_CLIENT_SECONDS,
    extract_bearer,
    issue_token,
    verify_token,
)


# ──────────────────────────────────────────────────────────────────
# core/auth_jwt — 순수 함수 단위 테스트.
# ──────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 1, 9, 30, tzinfo=timezone.utc)
_SECRET = "test-secret-do-not-use-in-prod"


def test_issue_admin_token_has_admin_ttl():
    out = issue_token("admin", now=_NOW, secret=_SECRET)
    assert out.role == "admin"
    assert out.ttl_sec == TTL_ADMIN_SECONDS
    assert out.expires_at - out.issued_at == timedelta(seconds=TTL_ADMIN_SECONDS)
    assert isinstance(out.access_token, str) and out.access_token.count(".") == 2


def test_issue_client_token_has_client_ttl():
    out = issue_token("client", now=_NOW, secret=_SECRET)
    assert out.role == "client"
    assert out.ttl_sec == TTL_CLIENT_SECONDS


def test_issue_invalid_role_raises():
    import pytest
    with pytest.raises(ValueError):
        issue_token("hacker", now=_NOW, secret=_SECRET)


def test_verify_returns_claims_for_valid_token():
    out = issue_token("admin", now=_NOW, secret=_SECRET)
    claims = verify_token(out.access_token, now=_NOW, secret=_SECRET)
    assert claims is not None
    assert claims.role == "admin"


def test_verify_returns_none_for_empty_token():
    assert verify_token("", secret=_SECRET) is None
    assert verify_token(None, secret=_SECRET) is None


def test_verify_returns_none_for_tampered_token():
    out = issue_token("admin", now=_NOW, secret=_SECRET)
    tampered = out.access_token[:-2] + ("AA" if out.access_token[-2:] != "AA" else "BB")
    assert verify_token(tampered, secret=_SECRET) is None


def test_verify_returns_none_for_wrong_secret():
    out = issue_token("admin", now=_NOW, secret=_SECRET)
    assert verify_token(out.access_token, secret="different-secret") is None


def test_verify_returns_none_for_expired_token():
    out = issue_token("admin", now=_NOW, secret=_SECRET, ttl_sec=10)
    later = _NOW + timedelta(seconds=20)
    assert verify_token(out.access_token, now=later, secret=_SECRET) is None


def test_extract_bearer_basic():
    assert extract_bearer("Bearer abc.def.ghi") == "abc.def.ghi"
    assert extract_bearer("bearer abc.def.ghi") == "abc.def.ghi"


def test_extract_bearer_rejects_non_bearer():
    assert extract_bearer("Basic xxxx") is None
    assert extract_bearer("") is None
    assert extract_bearer(None) is None


def test_extract_bearer_rejects_empty_token():
    assert extract_bearer("Bearer ") is None
    assert extract_bearer("Bearer") is None


# ──────────────────────────────────────────────────────────────────
# dashboard /api/auth 회귀.
# ──────────────────────────────────────────────────────────────────


def _flask_app():
    os.environ["DASHBOARD_ADMIN_PASSWORD"]  = "admin-test"
    os.environ["DASHBOARD_CLIENT_PASSWORD"] = "client-test"
    import importlib
    import config as _cfg
    importlib.reload(_cfg)
    import dashboard.app as dapp
    importlib.reload(dapp)
    dapp.app.config["TESTING"] = True
    return dapp


def test_api_login_wrong_password_returns_401():
    dapp = _flask_app()
    c = dapp.app.test_client()
    r = c.post("/api/auth/login", json={"password": "WRONG"})
    assert r.status_code == 401


def test_api_login_missing_password_returns_400():
    dapp = _flask_app()
    c = dapp.app.test_client()
    r = c.post("/api/auth/login", json={})
    assert r.status_code == 400


def test_api_login_admin_returns_token_and_role():
    dapp = _flask_app()
    c = dapp.app.test_client()
    r = c.post("/api/auth/login", json={"password": "admin-test"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert j["role"] == "admin"
    assert j["token_type"] == "Bearer"
    assert isinstance(j["access_token"], str) and j["access_token"]
    assert j["ttl_sec"] == TTL_ADMIN_SECONDS


def test_api_login_client_returns_token_and_role():
    dapp = _flask_app()
    c = dapp.app.test_client()
    r = c.post("/api/auth/login", json={"password": "client-test"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["role"] == "client"
    assert j["ttl_sec"] == TTL_CLIENT_SECONDS


def test_api_me_with_bearer_token():
    dapp = _flask_app()
    c = dapp.app.test_client()
    token = c.post("/api/auth/login", json={"password": "admin-test"}).get_json()["access_token"]
    c2 = dapp.app.test_client()   # 새 client — session 없음.
    r = c2.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["authenticated"] is True
    assert j["role"] == "admin"


def test_api_me_without_auth_returns_401():
    dapp = _flask_app()
    c = dapp.app.test_client()
    r = c.get("/api/auth/me")
    assert r.status_code == 401
    j = r.get_json()
    assert j["authenticated"] is False


def test_admin_endpoint_accepts_bearer_token():
    """JWT 만으로 admin endpoint 통과 가능 — session 우회 X, 정상 Bearer 인증."""
    dapp = _flask_app()
    c = dapp.app.test_client()
    token = c.post("/api/auth/login", json={"password": "admin-test"}).get_json()["access_token"]
    c2 = dapp.app.test_client()
    r = c2.get("/api/orders", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_admin_endpoint_rejects_client_token():
    dapp = _flask_app()
    c = dapp.app.test_client()
    token = c.post("/api/auth/login", json={"password": "client-test"}).get_json()["access_token"]
    c2 = dapp.app.test_client()
    r = c2.get("/api/orders", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_admin_endpoint_rejects_garbage_token():
    dapp = _flask_app()
    c = dapp.app.test_client()
    r = c.get("/api/orders", headers={"Authorization": "Bearer not.a.real.jwt"})
    assert r.status_code == 401


def test_logout_clears_session_only():
    """JWT blacklist 미구현 — server logout 은 session 만 비움 (의도)."""
    dapp = _flask_app()
    c = dapp.app.test_client()
    token = c.post("/api/auth/login", json={"password": "admin-test"}).get_json()["access_token"]
    # 로그아웃 후에도 token 자체는 남아있음 — client 가 폐기 책임.
    c.post("/api/auth/logout")
    c2 = dapp.app.test_client()
    r = c2.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200   # token 자체는 만료까지 유효.


def test_token_payload_has_no_sensitive_keys():
    """발급 토큰의 payload 에 비번/계정/secret 등 민감값 0건."""
    import jwt as _jwt
    out = issue_token("admin", now=_NOW, secret=_SECRET)
    # _NOW 가 fixed-clock (2026-05-01) — 실제 wall-clock 보다 과거일 수 있어 verify_exp 끔.
    payload = _jwt.decode(out.access_token, _SECRET, algorithms=["HS256"],
                          options={"verify_exp": False})
    blob = str(payload).lower()
    for forbidden in ("password", "appkey", "secret", "account_no", "token_secret"):
        assert forbidden not in blob, f"sensitive key in token payload: {forbidden}"
