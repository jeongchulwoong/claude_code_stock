"""
tests/test_client_react_mount.py — Phase F5 / F5.5 Flask wiring 회귀.

검증 범위:
  1) flag OFF → /client 에 client-react-root / /frontend-public-dist/ 부재. Jinja fallback 잔존.
  2) flag ON + manifest 정상 → mount root + data-phase="F5" + bundle script (type="module").
  3) flag ON + manifest 누락 → mount root 만, bundle 부재.
  4) /frontend-public-dist allowlist:
       client/vendor JS+CSS 200 / admin chunk 404 / .map 403 / .. traversal 403 / flag OFF 시 모두 404.
  5) /client 격리 — admin token / endpoint / mount root 0건.
  6) /advanced HTML 에 client React root 0건 (역방향 격리).
  7) /frontend-dist admin gate 유지 회귀.
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


def _patch_dist(dapp, manifest: dict | None, dist_dir: pathlib.Path,
                asset_files: dict[str, str] | None = None):
    dist_dir.mkdir(parents=True, exist_ok=True)
    vite = dist_dir / ".vite"
    vite.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (vite / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    if asset_files:
        for rel, body in asset_files.items():
            p = dist_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
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
            "name": "admin", "src": "apps/admin/index.html", "isEntry": True,
            "imports": ["_vendor-CAFEBABE.js"],
            "css": ["assets/admin-DEADBEEF.css"],
        },
        "apps/client/index.html": {
            "file": "assets/client-FACE0001.js",
            "name": "client", "src": "apps/client/index.html", "isEntry": True,
            "imports": ["_vendor-CAFEBABE.js"],
            "css": ["assets/client-FACE0001.css"],
        },
    }


def _full_assets() -> dict[str, str]:
    return {
        "assets/admin-DEADBEEF.js":   "/* admin */",
        "assets/admin-DEADBEEF.css":  "/* admin css */",
        "assets/client-FACE0001.js":  "/* client */",
        "assets/client-FACE0001.css": "/* client css */",
        "assets/vendor-CAFEBABE.js":  "/* vendor */",
        "assets/client-FACE0001.js.map": "{}",
    }


# ──────────────────────────────────────────────────────────────────────
# 1) flag OFF
# ──────────────────────────────────────────────────────────────────────

def test_client_no_react_when_flag_disabled():
    os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)
    os.environ.pop("FRONTEND_REACT_ENABLED", None)
    dapp = _flask_app()
    c = dapp.app.test_client()
    html = c.get("/client").get_data(as_text=True)
    assert 'id="client-react-root"' not in html
    assert "/frontend-public-dist/" not in html
    assert 'id="client-screener-list"' in html


# ──────────────────────────────────────────────────────────────────────
# 2) flag ON + manifest 정상
# ──────────────────────────────────────────────────────────────────────

def test_client_mounts_react_when_flag_enabled_and_manifest_ok():
    os.environ["FRONTEND_CLIENT_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp = ROOT / "db" / "test_tmp" / f"client_dist_{uuid.uuid4().hex}"
    orig = _patch_dist(dapp, _full_manifest(), tmp, _full_assets())
    try:
        c = dapp.app.test_client()
        html = c.get("/client").get_data(as_text=True)
        assert 'id="client-react-root"' in html
        assert 'data-phase="F5"' in html
        assert 'data-island="client-screener"' in html
        assert "/frontend-public-dist/assets/client-FACE0001.js" in html
        assert "/frontend-public-dist/assets/vendor-CAFEBABE.js" in html
        assert "/frontend-public-dist/assets/client-FACE0001.css" in html
        assert "/frontend-public-dist/assets/admin-DEADBEEF.js" not in html
        assert 'type="module"' in html
        assert 'id="client-screener-list"' in html   # Jinja fallback 잔존
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)


# ──────────────────────────────────────────────────────────────────────
# 3) flag ON + manifest 누락 → mount root 만
# ──────────────────────────────────────────────────────────────────────

def test_client_no_bundle_when_manifest_missing():
    os.environ["FRONTEND_CLIENT_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp = ROOT / "db" / "test_tmp" / f"client_dist_missing_{uuid.uuid4().hex}"
    orig = _patch_dist(dapp, None, tmp)
    try:
        c = dapp.app.test_client()
        html = c.get("/client").get_data(as_text=True)
        assert 'id="client-react-root"' in html
        assert "/frontend-public-dist/" not in html
        assert 'id="client-screener-list"' in html
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)


# ──────────────────────────────────────────────────────────────────────
# 4) /frontend-public-dist — 권한 / allowlist
# ──────────────────────────────────────────────────────────────────────

def test_public_dist_allows_client_and_vendor():
    os.environ["FRONTEND_CLIENT_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp = ROOT / "db" / "test_tmp" / f"client_assets_{uuid.uuid4().hex}"
    orig = _patch_dist(dapp, _full_manifest(), tmp, _full_assets())
    try:
        c = dapp.app.test_client()
        assert c.get("/frontend-public-dist/assets/client-FACE0001.js").status_code == 200
        assert c.get("/frontend-public-dist/assets/vendor-CAFEBABE.js").status_code == 200
        assert c.get("/frontend-public-dist/assets/client-FACE0001.css").status_code == 200
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)


def test_public_dist_blocks_admin_chunk():
    os.environ["FRONTEND_CLIENT_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp = ROOT / "db" / "test_tmp" / f"client_admin_block_{uuid.uuid4().hex}"
    orig = _patch_dist(dapp, _full_manifest(), tmp, _full_assets())
    try:
        c = dapp.app.test_client()
        assert c.get("/frontend-public-dist/assets/admin-DEADBEEF.js").status_code == 404
        assert c.get("/frontend-public-dist/assets/admin-DEADBEEF.css").status_code == 404
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)


def test_public_dist_blocks_source_map():
    os.environ["FRONTEND_CLIENT_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp = ROOT / "db" / "test_tmp" / f"client_map_block_{uuid.uuid4().hex}"
    orig = _patch_dist(dapp, _full_manifest(), tmp, _full_assets())
    try:
        c = dapp.app.test_client()
        assert c.get("/frontend-public-dist/assets/client-FACE0001.js.map").status_code == 403
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)


def test_public_dist_blocks_path_traversal():
    os.environ["FRONTEND_CLIENT_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp = ROOT / "db" / "test_tmp" / f"client_traversal_{uuid.uuid4().hex}"
    orig = _patch_dist(dapp, _full_manifest(), tmp, _full_assets())
    try:
        c = dapp.app.test_client()
        r = c.get("/frontend-public-dist/../etc/passwd")
        assert r.status_code in (403, 404), r.status_code
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)


def test_public_dist_404_when_flag_disabled():
    os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)
    dapp = _flask_app()
    tmp = ROOT / "db" / "test_tmp" / f"client_off_{uuid.uuid4().hex}"
    orig = _patch_dist(dapp, _full_manifest(), tmp, _full_assets())
    try:
        c = dapp.app.test_client()
        assert c.get("/frontend-public-dist/assets/client-FACE0001.js").status_code == 404
    finally:
        _restore_dist(dapp, orig)


# ──────────────────────────────────────────────────────────────────────
# 5) /client 격리
# ──────────────────────────────────────────────────────────────────────

def test_client_html_no_admin_tokens_with_both_flags_on():
    os.environ["FRONTEND_REACT_ENABLED"] = "1"
    os.environ["FRONTEND_CLIENT_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp = ROOT / "db" / "test_tmp" / f"client_iso_{uuid.uuid4().hex}"
    orig = _patch_dist(dapp, _full_manifest(), tmp, _full_assets())
    try:
        c = dapp.app.test_client()
        html = c.get("/client").get_data(as_text=True)
        for forbidden in (
            "/api/admin",
            "/api/admin/risk_events",
            "/api/orders",
            "/api/balance",
            "/api/portfolio",
            "/api/config",
            "/api/summary",
            "admin-overview-react-root",
            "admin-risk-events-react-root",
            "admin-realtime-react-root",
            "FRONTEND_REACT_ENABLED",
            "/frontend-public-dist/assets/admin-DEADBEEF.js",
            "/frontend-dist/",
        ):
            assert forbidden not in html, f"/client leaked admin token: {forbidden}"
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_REACT_ENABLED", None)
        os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)


# ──────────────────────────────────────────────────────────────────────
# 6) /advanced 에 client root 0건
# ──────────────────────────────────────────────────────────────────────

def test_advanced_html_no_client_react_root():
    os.environ["FRONTEND_REACT_ENABLED"] = "1"
    os.environ["FRONTEND_CLIENT_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp = ROOT / "db" / "test_tmp" / f"advanced_iso_{uuid.uuid4().hex}"
    orig = _patch_dist(dapp, _full_manifest(), tmp, _full_assets())
    try:
        c = _admin_client(dapp)
        html = c.get("/advanced").get_data(as_text=True)
        for forbidden in (
            "client-react-root",
            "/frontend-public-dist/",
            "FRONTEND_CLIENT_REACT_ENABLED",
        ):
            assert forbidden not in html, f"/advanced leaked client token: {forbidden}"
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_REACT_ENABLED", None)
        os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)


# ──────────────────────────────────────────────────────────────────────
# 7) /frontend-dist admin gate 유지
# ──────────────────────────────────────────────────────────────────────

def test_frontend_dist_admin_gate_still_enforced():
    os.environ["FRONTEND_CLIENT_REACT_ENABLED"] = "1"
    dapp = _flask_app()
    tmp = ROOT / "db" / "test_tmp" / f"admin_gate_{uuid.uuid4().hex}"
    orig = _patch_dist(dapp, _full_manifest(), tmp, _full_assets())
    try:
        anon = dapp.app.test_client()
        r1 = anon.get("/frontend-dist/assets/admin-DEADBEEF.js")
        assert r1.status_code in (302, 401), r1.status_code
        cs = _client_session(dapp)
        r2 = cs.get("/frontend-dist/assets/admin-DEADBEEF.js")
        assert r2.status_code == 403, r2.status_code
    finally:
        _restore_dist(dapp, orig)
        os.environ.pop("FRONTEND_CLIENT_REACT_ENABLED", None)


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
