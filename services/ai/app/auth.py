"""Supabase token verification.

The one rule: identity comes from a *verified signature*, never from the request.
There is no `user_id` field in any request body or query string in this service,
and no header other than `Authorization` is consulted — so there is nothing for a
caller to spoof and nothing for a document's contents to influence.

Both of Supabase's signing arrangements are supported, because which one a
project uses depends on when it was created:

  * symmetric HS256 with the project's JWT secret (local stacks, older projects);
  * asymmetric ES256/RS256 with keys published at the project's JWKS endpoint.

Verification checks signature, expiry, not-before, audience, issuer when one is
configured, and that the subject is a UUID with the `authenticated` role. An
anonymous or service token is rejected here rather than being allowed to reach a
policy that would treat it as some user.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWKClient

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")


@dataclass(frozen=True)
class Caller:
    """A verified caller.

    `claims` is the trimmed claim set handed to `Database.user_scope`, and it is
    deliberately *not* where the raw token lives: those claims are written into
    `request.jwt.claims` inside Postgres, and a bearer token has no business being
    there.

    `access_token` is kept separately, with `repr=False`, because the only thing it
    is for is calling Supabase Storage on the caller's behalf. Excluding it from the
    repr means an accidental `logger.info("%s", caller)` cannot leak it.
    """

    user_id: uuid.UUID
    email: str | None
    claims: dict[str, Any]
    access_token: str = field(repr=False, default="")


class _JwksCache:
    """Small TTL cache in front of the project's JWKS endpoint.

    Refetching on every request would put an outbound HTTP call in the hot path
    and make the service fail closed whenever the network hiccups. Keys rotate on
    the order of months; a ten-minute cache is the right trade, and an unknown
    `kid` forces an immediate refresh so a rotation does not cause an outage.
    """

    def __init__(self) -> None:
        self._client: PyJWKClient | None = None
        self._url: str | None = None
        self._fetched_at: float = 0.0

    def get(self, url: str, ttl_seconds: int) -> PyJWKClient:
        now = time.monotonic()
        if self._client is None or self._url != url or now - self._fetched_at > ttl_seconds:
            # PyJWKClient does its own lifespan caching; a fresh instance is how
            # we force a refetch after the TTL.
            self._client = PyJWKClient(url, cache_keys=True, lifespan=ttl_seconds)
            self._url = url
            self._fetched_at = now
        return self._client

    def invalidate(self) -> None:
        self._client = None


_jwks_cache = _JwksCache()


def jwks_url(settings: Settings) -> str:
    if settings.supabase_jwks_url:
        return settings.supabase_jwks_url
    return f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify a Supabase access token and return its claims.

    Raises HTTPException(401) for anything that is not a currently valid token
    for an authenticated end user.
    """
    options = {
        "require": ["exp", "sub"],
        "verify_signature": True,
        "verify_exp": True,
        "verify_nbf": True,
        "verify_aud": True,
    }
    common: dict[str, Any] = {
        "audience": settings.supabase_jwt_audience,
        "options": options,
        # A little clock skew, but not enough to meaningfully extend a token.
        "leeway": 10,
    }
    if settings.supabase_jwt_issuer:
        common["issuer"] = settings.supabase_jwt_issuer

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise _unauthorized("Malformed access token.") from exc

    algorithm = header.get("alg")

    try:
        if algorithm == "HS256":
            if not settings.supabase_jwt_secret:
                # Refusing is the only safe answer: the alternative is accepting
                # a token nobody can check.
                logger.error("received an HS256 token but SUPABASE_JWT_SECRET is not set")
                raise _unauthorized("Token cannot be verified by this service.")
            claims = jwt.decode(
                token, settings.supabase_jwt_secret, algorithms=["HS256"], **common
            )
        elif algorithm in _ASYMMETRIC_ALGORITHMS:
            url = jwks_url(settings)
            try:
                signing_key = _jwks_cache.get(url, settings.jwks_cache_seconds).get_signing_key_from_jwt(token)
            except jwt.PyJWKClientError:
                # Most likely a rotation: drop the cache and try once more.
                _jwks_cache.invalidate()
                signing_key = _jwks_cache.get(url, settings.jwks_cache_seconds).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=list(_ASYMMETRIC_ALGORITHMS), **common
            )
        else:
            raise _unauthorized(f"Unsupported token algorithm: {algorithm!r}")
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Access token has expired.") from exc
    except (jwt.InvalidTokenError, httpx.HTTPError) as exc:
        logger.info("token rejected: %s", type(exc).__name__)
        raise _unauthorized("Invalid access token.") from exc

    role = claims.get("role")
    if role != "authenticated":
        # Anonymous sign-ins and service tokens are valid JWTs but are not an end
        # user, and the policies would treat a service token as "no uid at all".
        raise _unauthorized("This endpoint requires a signed-in user.")

    try:
        uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise _unauthorized("Access token has no usable subject.") from exc

    return claims


async def current_caller(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Caller:
    """FastAPI dependency: the verified caller, or 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Missing bearer token.")

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise _unauthorized("Missing bearer token.")

    claims = verify_token(token, settings)

    # Only the claims Postgres needs are forwarded. Trimming keeps a large token
    # out of every `set_config` call and keeps unrelated claims out of SQL.
    scoped = {
        "sub": str(claims["sub"]),
        "role": "authenticated",
        "aud": settings.supabase_jwt_audience,
    }
    if claims.get("email"):
        scoped["email"] = str(claims["email"])

    return Caller(
        user_id=uuid.UUID(str(claims["sub"])),
        email=claims.get("email"),
        claims=scoped,
        access_token=token,
    )
