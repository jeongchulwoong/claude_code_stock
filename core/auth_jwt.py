"""
core/auth_jwt.py — Dashboard React UI 로그인용 JWT 발급/검증.

설계 원칙:
  - 기존 Flask session-cookie 인증과 **병행**. JWT 는 추가될 뿐 기존 동작은 변경 0줄.
  - 비밀키 우선순위:
      1) JWT_SECRET_KEY env
      2) DASHBOARD_SECRET_KEY env
      3) 프로세스 시작 시 1회 random 생성 (재시작 시 모든 토큰 무효 — 의도한 안전 default)
  - 토큰 페이로드: { sub: 'admin'|'client', iat, exp }. 민감정보 0건.
  - 토큰 만료: admin 8시간 / client 24시간 (긴 세션이지만 절대 무한대 X).
  - 토큰 검증 실패는 모두 None 반환 — raise 하지 않는다 (호출자가 401 결정).

테스트 결정성:
  - issue_token / verify_token 모두 시간 인자를 받아 fixed-clock 가능.
  - 비밀키도 인자로 override 가능.
"""

from __future__ import annotations

import os
import secrets as _secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final, Literal

import jwt

# JWT 알고리즘 — HS256 만 허용. RS256 등 비대칭은 본 phase 에서 사용 안 함.
_ALGORITHM: Final[str] = "HS256"
_ALLOWED_ROLES: Final[frozenset[str]] = frozenset({"admin", "client"})

# 만료 정책 (초).
TTL_ADMIN_SECONDS: Final[int] = 8 * 60 * 60     # 8h
TTL_CLIENT_SECONDS: Final[int] = 24 * 60 * 60   # 24h

# 모듈 로드 1회만 생성되는 fallback 비밀 — 재시작 시 모든 토큰 무효.
_FALLBACK_SECRET: Final[str] = _secrets.token_hex(32)


@dataclass(frozen=True)
class TokenIssued:
    access_token: str
    role: str
    issued_at: datetime
    expires_at: datetime
    ttl_sec: int


@dataclass(frozen=True)
class TokenClaims:
    role: str
    issued_at: datetime
    expires_at: datetime


def _resolve_secret(override: str | None = None) -> str:
    """비밀키 결정 — override > JWT_SECRET_KEY > DASHBOARD_SECRET_KEY > random fallback."""
    if override:
        return override
    return (
        os.getenv("JWT_SECRET_KEY")
        or os.getenv("DASHBOARD_SECRET_KEY")
        or _FALLBACK_SECRET
    )


def _ttl_for_role(role: str) -> int:
    return TTL_ADMIN_SECONDS if role == "admin" else TTL_CLIENT_SECONDS


def issue_token(
    role: Literal["admin", "client"] | str,
    *,
    now: datetime | None = None,
    secret: str | None = None,
    ttl_sec: int | None = None,
) -> TokenIssued:
    """role 기반 JWT 발급. role 이 화이트리스트 외이면 ValueError."""
    role_norm = str(role or "").lower()
    if role_norm not in _ALLOWED_ROLES:
        raise ValueError(f"unsupported role: {role!r}")
    issued = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    ttl = int(ttl_sec) if ttl_sec is not None else _ttl_for_role(role_norm)
    if ttl <= 0:
        raise ValueError(f"ttl_sec must be positive, got {ttl}")
    expires = issued + timedelta(seconds=ttl)
    payload = {
        "sub": role_norm,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    token = jwt.encode(payload, _resolve_secret(secret), algorithm=_ALGORITHM)
    return TokenIssued(
        access_token=token,
        role=role_norm,
        issued_at=issued,
        expires_at=expires,
        ttl_sec=ttl,
    )


def verify_token(
    token: str | None,
    *,
    now: datetime | None = None,
    secret: str | None = None,
) -> TokenClaims | None:
    """토큰 검증. 실패 시 None — raise 하지 않는다.

    실패 사유:
      - token 비어있음
      - 서명 불일치 / payload 변조
      - exp 만료
      - role 화이트리스트 외
    """
    if not token:
        return None
    # `now` 가 명시되면 caller 가 시간 검증 책임 — PyJWT 의 auto-exp 를 끄고 manual 검사.
    try:
        if now is not None:
            payload = jwt.decode(
                token,
                _resolve_secret(secret),
                algorithms=[_ALGORITHM],
                options={"verify_exp": False},
            )
        else:
            payload = jwt.decode(
                token,
                _resolve_secret(secret),
                algorithms=[_ALGORITHM],
            )
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    role = str(payload.get("sub") or "").lower()
    if role not in _ALLOWED_ROLES:
        return None
    try:
        iat = int(payload.get("iat") or 0)
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    issued = datetime.fromtimestamp(iat, tz=timezone.utc)
    expires = datetime.fromtimestamp(exp, tz=timezone.utc)
    # PyJWT 가 exp 를 이미 검증하지만, now override 시 이중 체크.
    if now is not None:
        n = now.astimezone(timezone.utc)
        if n >= expires:
            return None
    return TokenClaims(role=role, issued_at=issued, expires_at=expires)


def extract_bearer(header: str | None) -> str | None:
    """`Authorization: Bearer <token>` 헤더 → 토큰 문자열. 형식 외는 None."""
    if not header:
        return None
    s = str(header).strip()
    if not s.lower().startswith("bearer "):
        return None
    rest = s[7:].strip()
    return rest or None
