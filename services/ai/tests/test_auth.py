"""Token verification. Identity is a verified signature or it is nothing."""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
from fastapi import HTTPException

from app.auth import jwks_url, verify_token
from app.config import Settings

SECRET = "test-jwt-secret-not-a-real-one-padded-to-32b"


def _settings(**overrides) -> Settings:
    return Settings(supabase_jwt_secret=SECRET, **overrides)


def _token(secret: str = SECRET, algorithm: str = "HS256", **claims) -> str:
    payload = {
        "sub": str(uuid.uuid4()),
        "role": "authenticated",
        "aud": "authenticated",
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
    }
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm=algorithm)


def test_a_valid_token_is_accepted():
    claims = verify_token(_token(), _settings())
    assert claims["role"] == "authenticated"
    uuid.UUID(claims["sub"])


def test_an_expired_token_is_rejected():
    with pytest.raises(HTTPException) as caught:
        verify_token(_token(exp=int(time.time()) - 60), _settings())
    assert caught.value.status_code == 401


def test_a_token_signed_with_the_wrong_secret_is_rejected():
    with pytest.raises(HTTPException) as caught:
        verify_token(_token(secret="a-different-secret-also-at-least-32-bytes"), _settings())
    assert caught.value.status_code == 401


def test_a_token_for_the_wrong_audience_is_rejected():
    with pytest.raises(HTTPException):
        verify_token(_token(aud="some-other-service"), _settings())


def test_an_unsigned_token_is_rejected():
    """The classic `alg: none` downgrade."""
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "role": "authenticated", "aud": "authenticated",
         "exp": int(time.time()) + 600},
        key="",
        algorithm="none",
    )
    with pytest.raises(HTTPException):
        verify_token(forged, _settings())


def test_an_anonymous_or_service_token_is_not_an_end_user():
    # Valid signatures, but neither is a signed-in person, and the policies would
    # treat a service token as "no uid at all".
    for role in ("anon", "service_role"):
        with pytest.raises(HTTPException) as caught:
            verify_token(_token(role=role), _settings())
        assert caught.value.status_code == 401


def test_a_token_without_a_usable_subject_is_rejected():
    with pytest.raises(HTTPException):
        verify_token(_token(sub="not-a-uuid"), _settings())
    with pytest.raises(HTTPException):
        # `sub` is in the `require` list, so this fails before the UUID check.
        verify_token(jwt.encode({"role": "authenticated", "aud": "authenticated",
                                 "exp": int(time.time()) + 60}, SECRET, algorithm="HS256"),
                     _settings())


def test_a_wrong_issuer_is_rejected_when_one_is_configured():
    settings = _settings(supabase_jwt_issuer="https://project.supabase.co/auth/v1")
    with pytest.raises(HTTPException):
        verify_token(_token(iss="https://evil.example/auth/v1"), settings)
    # And the right one passes.
    claims = verify_token(_token(iss="https://project.supabase.co/auth/v1"), settings)
    assert claims["iss"] == "https://project.supabase.co/auth/v1"


def test_an_hs256_token_is_refused_when_no_secret_is_configured():
    """Refusing beats accepting a token nobody can check."""
    settings = Settings(supabase_jwt_secret=None)
    with pytest.raises(HTTPException) as caught:
        verify_token(_token(), settings)
    assert caught.value.status_code == 401


def test_a_malformed_token_is_rejected():
    for bad in ("", "abc", "a.b.c", "Bearer x"):
        with pytest.raises(HTTPException):
            verify_token(bad, _settings())


def test_the_jwks_url_is_derived_from_the_project_url():
    settings = Settings(supabase_url="https://abc.supabase.co/")
    assert jwks_url(settings) == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
    override = Settings(supabase_jwks_url="https://keys.example/jwks.json")
    assert jwks_url(override) == "https://keys.example/jwks.json"


def test_an_asymmetric_token_is_verified_against_the_projects_jwks(monkeypatch):
    """ES256 path, with the key server stubbed rather than the verification."""
    from cryptography.hazmat.primitives.asymmetric import ec

    from app import auth as auth_module

    private_key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 600,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )

    class _Key:
        key = private_key.public_key()

    class _Client:
        def get_signing_key_from_jwt(self, _token):
            return _Key()

    monkeypatch.setattr(auth_module._jwks_cache, "get", lambda *_args, **_kwargs: _Client())

    claims = verify_token(token, Settings(supabase_jwt_secret=None))
    assert claims["role"] == "authenticated"

    # A token signed by a different key must not pass.
    other_key = ec.generate_private_key(ec.SECP256R1())
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 600,
        },
        other_key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )
    with pytest.raises(HTTPException):
        verify_token(forged, Settings(supabase_jwt_secret=None))


def test_the_raw_token_is_not_in_the_claims_handed_to_postgres():
    """The claims dict is written into request.jwt.claims; a bearer token must not be."""
    import asyncio

    from app.auth import current_caller

    token = _token(email="person@example.test")
    caller = asyncio.run(current_caller(authorization=f"Bearer {token}", settings=_settings()))

    assert set(caller.claims) <= {"sub", "role", "aud", "email"}
    assert token not in str(caller.claims)
    assert caller.access_token == token
    # And it is kept out of the repr, so an accidental log line cannot leak it.
    assert token not in repr(caller)
