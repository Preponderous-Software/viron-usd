"""Mint short-lived HS256 JWT bearer tokens for a locally started test Viron."""

from __future__ import annotations

import time

import jwt

DEFAULT_SECRET = "viron-usd-integration-test-secret-0123456789"
DEFAULT_ISSUER = "viron-usd-test"


def mint_token(
    secret: str = DEFAULT_SECRET,
    issuer: str = DEFAULT_ISSUER,
    *,
    subject: str = "viron-usd-tests",
    ttl_seconds: int = 900,
    now: float | None = None,
) -> str:
    """An HS256 token signed with the UTF-8 bytes of ``secret`` whose ``iss`` is ``issuer``."""
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("HS256 secrets must be at least 32 bytes for Viron")
    issued = int(time.time() if now is None else now)
    claims = {"iss": issuer, "sub": subject, "iat": issued, "exp": issued + int(ttl_seconds)}
    return jwt.encode(claims, secret.encode("utf-8"), algorithm="HS256")
