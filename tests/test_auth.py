"""Token verification. No database, no HTTP, no network — the JWK client is stubbed.

Supabase signs access tokens with ES256 and publishes the public key at the project's
JWKS endpoint, so these tests generate a throwaway P-256 key and hand its public half to
a fake client. Nothing here touches the real project.
"""

import base64
import datetime
import hashlib
import hmac
import json
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.security import HTTPAuthorizationCredentials
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app.auth import current_user_id
from app.config import Settings
from app.services.errors import AuthUnavailableError, UnauthenticatedError

ISSUER = "https://test-project.supabase.co/auth/v1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@pytest.fixture
def private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture(autouse=True)
def stub_jwk_client(monkeypatch, private_key):
    """Stand in for the network fetch. Individual tests override this to fail."""

    class FakeKey:
        key = private_key.public_key()

    class FakeClient:
        def get_signing_key_from_jwt(self, token: str) -> FakeKey:
            return FakeKey()

    monkeypatch.setattr("app.auth.get_jwk_client", FakeClient)


def make_token(private_key: ec.EllipticCurvePrivateKey, **overrides: object) -> str:
    claims: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="ES256")


def bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_a_valid_token_yields_its_subject(private_key, settings):
    user_id = uuid.uuid4()

    result = current_user_id(bearer(make_token(private_key, sub=str(user_id))), settings)

    assert result == user_id


def test_a_missing_header_is_unauthenticated(settings):
    with pytest.raises(UnauthenticatedError) as caught:
        current_user_id(None, settings)

    assert caught.value.code == "unauthenticated"


def test_an_expired_token_is_rejected(private_key, settings):
    expired = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)

    with pytest.raises(UnauthenticatedError):
        current_user_id(bearer(make_token(private_key, exp=expired)), settings)


def test_a_token_for_another_audience_is_rejected(private_key, settings):
    with pytest.raises(UnauthenticatedError):
        current_user_id(bearer(make_token(private_key, aud="anon")), settings)


def test_a_token_from_another_issuer_is_rejected(private_key, settings):
    """A trailing slash in SUPABASE_URL lands here, which is why the config strips it."""
    with pytest.raises(UnauthenticatedError):
        current_user_id(bearer(make_token(private_key, iss=f"{ISSUER}/")), settings)


def test_a_token_signed_by_another_key_is_rejected(settings):
    """The signature is genuinely checked, not merely decoded."""
    attacker_key = ec.generate_private_key(ec.SECP256R1())

    with pytest.raises(UnauthenticatedError):
        current_user_id(bearer(make_token(attacker_key)), settings)


def test_an_hs256_token_is_rejected_even_when_signed_with_the_public_key(
    private_key, settings
):
    """Algorithm confusion, the classic JWT attack, and why the algorithm list is one long.

    The verifying key is public by definition. An attacker who reuses it as an HMAC
    secret mints tokens for any user — unless the accepted algorithms exclude HS256.
    The token is assembled by hand because PyJWT refuses to encode this shape at all.
    """
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": int((datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)).timestamp()),
    }
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    signature = _b64(
        hmac.new(public_pem, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )

    with pytest.raises(UnauthenticatedError):
        current_user_id(bearer(f"{header}.{payload}.{signature}"), settings)


def test_a_token_with_no_subject_is_rejected(private_key, settings):
    with pytest.raises(UnauthenticatedError):
        current_user_id(bearer(make_token(private_key, sub="")), settings)


def test_a_non_uuid_subject_is_rejected(private_key, settings):
    with pytest.raises(UnauthenticatedError):
        current_user_id(bearer(make_token(private_key, sub="not-a-uuid")), settings)


def test_an_unknown_signing_key_is_a_401_not_a_500(monkeypatch, private_key, settings):
    """PyJWKClientError is not an InvalidTokenError, so it needs its own except."""

    class FailingClient:
        def get_signing_key_from_jwt(self, token: str) -> object:
            raise PyJWKClientError('Unable to find a signing key that matches: "abc"')

    monkeypatch.setattr("app.auth.get_jwk_client", FailingClient)

    with pytest.raises(UnauthenticatedError) as caught:
        current_user_id(bearer(make_token(private_key)), settings)

    assert caught.value.status_code == 401


def test_an_unreachable_jwks_endpoint_is_a_503_not_a_401(monkeypatch, private_key, settings):
    """A Supabase outage must not tell four users they have been logged out."""

    class UnreachableClient:
        def get_signing_key_from_jwt(self, token: str) -> object:
            raise PyJWKClientConnectionError("connection refused")

    monkeypatch.setattr("app.auth.get_jwk_client", UnreachableClient)

    with pytest.raises(AuthUnavailableError) as caught:
        current_user_id(bearer(make_token(private_key)), settings)

    assert caught.value.status_code == 503
    assert caught.value.code == "auth_unavailable"
