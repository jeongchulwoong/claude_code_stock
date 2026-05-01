"""
tests/test_advanced_react_mount.py — Phase F3/F4 React island Flask wiring 회귀 테스트.

검증 범위:
  1) flag OFF → /advanced 에 admin-{overview,risk-events,realtime}-react-root 부재 + bundle 부재.
  2) flag ON + manifest 정상 → 3 mount root + bundle script (type="module") + F3/F4 마커.
  3) flag ON + manifest 누락 → 3 mount root 만 출력, bundle 부재 (Jinja fallback).
  4) /frontend-dist/<path> 익명/client 세션 → 401/302 / 403 (admin_required 유지).
  5) /client 페이지에 admin-* root, /frontend-dist, /api/admin 토큰 0건.

본 테스트는 실제 frontend/dist 산출물을 만들거나 지우지 않는다 — manifest + 임시 dir monkey-patch.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _flask_app():
    os.environ.setdefault("DASHBOARD_ADMIN_PASSWORD",  "admin-test")
    os.environ.setdefault("DASHBOARD_CLIENT_PASSWORD", "client-test")
    import importlib
    import config as _cfg
    importlib.reload(_cfg)
    import dashboard.app as dapp
    importlib.reload(dapp)
    dapp.app.config["TESTING"] = True
    return dapp


def _admin_client(dapp):
    c = dapp.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["role"] = "admin"
    return c


def _client_session(dapp):
    c = dapp.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["role"] = "client"
    return c


def _patch_manifest(dapp, manifest: dict | None, dist_dir: pathlib.Path):
    dist_dir.mkdir(parents=True, exist_ok=True)
    vite_dir = dist_dir / ".vite"
    vite_dir.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (vite_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8",
        )
    orig = dapp._FRONTEND_DIST
    dapp._FRONTEND_DIST = dist_dir
    return orig


def _restore_dist(dapp, orig) -> None:
    dapp._FRONTEND_DIST = orig


def _full_manifest() -> dict:
    return {
        "_vendor-CAFEBABE.js": {"file": "assets/vendor-CAFEBABE.js", "name": "vendor"},
        "apps/admin/index.html": {
            "file": "assets/admin-DEADBEEF.js",
            "name": "admin",
            "src": "apps/admin/index.html",
            "isEntry": True,
            "imports": ["_vendor-CAFEBABE.js"],
            "css": ["assets/admin-DEADBEEF.css"],
        },
    }


# ──────────────────────────────────────────────────────────────────────
# 1) flag OFF → React asset 미주입
# ──────────────────────────────────────────────────────────────────────

def test_advanced_no_react_when_flag_disabled():
    os.environ.pop("FRONTEND_REACT_ENABLED", None)
    dapp = _flask_app()
    c = _admin_client(dapp)
    html = c.get("/advanced").get_data(as_text=True)
    assert 'id="admin-overview-react-root"' not in html
    assert 'id="admin-risk-events-react-root"' not in html
    assert 'id="admin-realtime-react-root"' not in html
    assert "/frontend-dist/" not in html


# ──────────────────────────────────────────────────────────────────────
# 2) flag ON + manifest 정상 → 3 mount root + bundle 출력
# ──────────────────────────────────────────────────────────────────────

def test_advanced_mounts_react_when_flag_enabled_and_manifest_ok():
    os.environ["FRONTEND_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp_dist = ROOT / "db" / "test_tmp" / f"react_dist_{uuid.uuid4().hex}"
    orig = _patch_manifest(dapp, _full_manifest(), tmp_dist)
    try:
        c = _admin_client(dapp)
        html = c.get("/advanced").get_data(as_text=True)
        assert 'id="admin-overview-react-root"'    in html
        assert 'id="admin-risk-events-react-root"' in html
        assert 'id="admin-realtime-react-root"'    in html
        assert 'data-phase="F3"' in html
        assert 'data-phase="F4"' in html
        assert "/frontend-dist/assets/admin-DEADBEEF.js"  in html
        assert "/frontend-dist/assets/vendor-CAFEBABE.js" in html
        assert "/frontend-dist/assets/admin-DEADBEEF.css" in html
        assert 'type="module"' in html
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_REACT_ENABLED", None)


# ──────────────────────────────────────────────────────────────────────
# 3) flag ON + manifest 누락 → mount root 만, bundle 없음
# ──────────────────────────────────────────────────────────────────────

def test_advanced_no_bundle_when_manifest_missing():
    os.environ["FRONTEND_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp_dist = ROOT / "db" / "test_tmp" / f"react_dist_missing_{uuid.uuid4().hex}"
    orig = _patch_manifest(dapp, None, tmp_dist)
    try:
        c = _admin_client(dapp)
        html = c.get("/advanced").get_data(as_text=True)
        assert 'id="admin-overview-react-root"'    in html
        assert 'id="admin-risk-events-react-root"' in html
        assert 'id="admin-realtime-react-root"'    in html
        assert "/frontend-dist/" not in html
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_REACT_ENABLED", None)


# ──────────────────────────────────────────────────────────────────────
# 4) /frontend-dist 격리 — 익명/client 차단
# ──────────────────────────────────────────────────────────────────────

def test_frontend_dist_anon_redirects_or_401():
    dapp = _flask_app()
    c = dapp.app.test_client()
    r = c.get("/frontend-dist/assets/admin-DEADBEEF.js")
    assert r.status_code in (302, 401), r.status_code


def test_frontend_dist_client_session_returns_403():
    dapp = _flask_app()
    c = _client_session(dapp)
    r = c.get("/frontend-dist/assets/admin-DEADBEEF.js")
    assert r.status_code == 403, r.status_code


# ──────────────────────────────────────────────────────────────────────
# 5) /client 격리 — admin React 토큰 0건
# ──────────────────────────────────────────────────────────────────────

def test_client_html_no_react_admin_tokens():
    """flag 가 켜져 있어도 /client 에 admin React 마커 0건."""
    os.environ["FRONTEND_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp_dist = ROOT / "db" / "test_tmp" / f"react_dist_client_{uuid.uuid4().hex}"
    orig = _patch_manifest(dapp, _full_manifest(), tmp_dist)
    try:
        c = dapp.app.test_client()
        html = c.get("/client").get_data(as_text=True)
        for forbidden in (
            "admin-overview-react-root",
            "admin-risk-events-react-root",
            "admin-realtime-react-root",
            "/frontend-dist/",
            "/api/admin",
            "/api/admin/risk_events",
            "/api/orders",
            "/api/balance",
            "/api/portfolio",
            "FRONTEND_REACT_ENABLED",
        ):
            assert forbidden not in html, f"/client 에 admin React 토큰 노출: {forbidden}"
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_REACT_ENABLED", None)


if __name__ == "__main__":
    import inspect
    import traceback
    mod = sys.modules[__name__]
    fns = [(n, f) for n, f in inspect.getmembers(mod) if n.startswith("test_") and inspect.isfunction(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"  ok   {n}")
        except AssertionError as e:
            print(f"  FAIL {n}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {n}: {e!r}")
            traceback.print_exc()
            failed += 1
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(0 if failed == 0 else 1)
