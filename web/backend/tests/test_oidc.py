from __future__ import annotations

from datetime import timedelta
import json
import time
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest
from sqlalchemy import select

from oidc_helpers import FakeGoogleClient, TestSettings
from test_api import build_client, login
from veilway_control.config import Settings
from veilway_control.models import GoogleAdminBinding, OAuthLoginAttempt, User, UserSession, utcnow
from veilway_control.oidc import (
    GOOGLE_JWKS_URL, GOOGLE_TOKEN_URL, GoogleIdentity, GoogleOidcClient, OidcRejected,
)
from veilway_control.security import hash_token


def start(client):
    response = client.get("/api/v1/auth/google/start", follow_redirects=False)
    assert response.status_code == 303
    return response, {key: values[0] for key, values in parse_qs(urlsplit(response.headers["location"]).query).items()}


def callback(client, state, **extra):
    return client.get("/api/v1/auth/google/callback", params={"state": state, "code": "test-code", **extra},
                      follow_redirects=False)


def test_start_uses_minimal_scopes_fixed_callback_and_hashes(db_factory):
    with build_client(db_factory) as client:
        response, params = start(client)
        assert params["scope"] == "openid email" and params["response_type"] == "code"
        assert params["redirect_uri"] == "https://testserver/api/v1/auth/google/callback"
        assert "access_type" not in params and "login_hint" not in params
        assert urlsplit(response.headers["location"]).netloc == "accounts.google.com"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
        browser = client.cookies.get("__Host-veilway_oauth")
        with db_factory() as db:
            attempt = db.scalar(select(OAuthLoginAttempt))
            assert attempt.state_hash == hash_token(params["state"])
            assert attempt.nonce_hash == hash_token(params["nonce"])
            assert attempt.browser_hash == hash_token(browser)
        _, second = start(client)
        assert second["state"] != params["state"] and second["nonce"] != params["nonce"]
        with db_factory() as db:
            assert len(db.scalars(select(OAuthLoginAttempt)).all()) == 1


@pytest.mark.parametrize("identity,role", [
    (GoogleIdentity("operator-sub", "operator@gmail.com", True), "ADMIN"),
    (GoogleIdentity("regular-sub", "regular@example.invalid", False), "USER"),
])
def test_first_repeat_login_session_rotation_and_logout(db_factory, identity, role):
    provider = FakeGoogleClient(identity)
    with build_client(db_factory, oidc_client=provider) as client:
        login(client)
        first = client.get("/api/v1/auth/session")
        assert first.headers["Cache-Control"] == "no-store"
        assert set(first.json()) == {"user_id", "email", "role", "csrf_token"}
        assert first.json()["email"] == identity.email and first.json()["role"] == role
        token = client.cookies.get("__Host-veilway_session")
        csrf = login(client)
        assert client.cookies.get("__Host-veilway_session") != token
        assert client.get("/api/v1/auth/session").json()["user_id"] == first.json()["user_id"]
        csrf = client.get("/api/v1/auth/session").json()["csrf_token"]
        assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
        assert client.get("/api/v1/auth/session").status_code == 401
    with db_factory() as db:
        assert len(db.scalars(select(User)).all()) == 1
        assert db.scalars(select(OAuthLoginAttempt)).all() == []
        binding = db.get(GoogleAdminBinding, 1)
        assert (binding.user_id is not None) == (role == "ADMIN")


def test_admin_is_pinned_by_subject_and_never_transferred_by_email(db_factory):
    provider = FakeGoogleClient()
    with build_client(db_factory, oidc_client=provider) as client:
        login(client)
        admin_id = client.get("/api/v1/auth/session").json()["user_id"]
        provider.identity = GoogleIdentity("other-sub", "operator@gmail.com", True)
        login(client)
        second = client.get("/api/v1/auth/session").json()
        assert second["role"] == "USER" and second["user_id"] != admin_id
        assert client.get("/api/v1/vpn-vms").status_code == 403
        provider.identity = GoogleIdentity("operator-sub", "changed@gmail.com", True)
        login(client)
        updated = client.get("/api/v1/auth/session").json()
        assert updated["role"] == "ADMIN" and updated["user_id"] == admin_id
        assert updated["email"] == "changed@gmail.com"


def test_legacy_identity_is_never_adopted(db_factory, seed_control_data):
    ids = seed_control_data(legacy_admin=True)
    with build_client(db_factory) as client:
        login(client)
        assert client.get("/api/v1/auth/session").json()["user_id"] != str(ids["user_id"])
    with db_factory() as db:
        assert db.get(User, ids["user_id"]).google_sub is None
        assert len(db.scalars(select(User)).all()) == 2


@pytest.mark.parametrize("invalid", ["state", "cookie", "no_cookie", "expired", "duplicate_state", "duplicate_code"])
def test_invalid_or_expired_browser_bound_attempt_never_exchanges_code(db_factory, invalid):
    provider = FakeGoogleClient()
    with build_client(db_factory, oidc_client=provider) as client:
        _, params = start(client)
        if invalid == "state":
            params["state"] = "x" * 43
        elif invalid == "cookie":
            client.cookies.clear()
            client.cookies.set("__Host-veilway_oauth", "x" * 43)
        elif invalid == "no_cookie":
            client.cookies.clear()
        elif invalid == "expired":
            with db_factory() as db:
                db.scalar(select(OAuthLoginAttempt)).expires_at = utcnow() - timedelta(seconds=1)
                db.commit()
        query = [("state", params["state"]), ("code", "test-code")]
        if invalid.startswith("duplicate_"):
            query.append((invalid.removeprefix("duplicate_"), "another-value"))
        response = client.get("/api/v1/auth/google/callback", params=query, follow_redirects=False)
        assert response.headers["location"] == "/?auth_error=login_failed"
        assert "__Host-veilway_session=" not in response.headers.get("set-cookie", "")
        assert provider.exchanges == 0
    with db_factory() as db:
        assert db.scalars(select(UserSession)).all() == []


def test_callback_replay_is_rejected_even_with_original_browser_cookie(db_factory):
    provider = FakeGoogleClient()
    with build_client(db_factory, oidc_client=provider) as client:
        _, params = start(client)
        browser = client.cookies.get("__Host-veilway_oauth")
        assert callback(client, params["state"]).headers["location"] == "/"
        client.cookies.clear()
        client.cookies.set("__Host-veilway_oauth", browser)
        assert callback(client, params["state"]).headers["location"] == "/?auth_error=login_failed"
        assert provider.exchanges == 1


@pytest.mark.parametrize("failure", ["provider", "cancelled", "disabled_user", "untrusted_admin_email"])
def test_failed_login_does_not_register_or_create_session_and_consumes_attempt(db_factory, failure):
    provider = FakeGoogleClient()
    if failure == "provider":
        provider.rejected = True
    if failure == "untrusted_admin_email":
        provider.identity = GoogleIdentity("operator-sub", "operator@gmail.com", False)
    if failure == "disabled_user":
        with db_factory() as db:
            db.add(User(google_sub="operator-sub", email="operator@gmail.com", is_active=False))
            db.commit()
    with build_client(db_factory, oidc_client=provider) as client:
        _, params = start(client)
        extra = {"error": "access_denied", "error_description": "do-not-reflect-provider-details"} if failure == "cancelled" else {}
        response = callback(client, params["state"], **extra)
        assert response.headers["location"] == "/?auth_error=" + ("cancelled" if failure == "cancelled" else "login_failed")
        assert "do-not-reflect" not in response.text + str(response.headers)
    with db_factory() as db:
        assert db.scalars(select(UserSession)).all() == []
        assert db.scalars(select(OAuthLoginAttempt)).all() == []
        assert len(db.scalars(select(User)).all()) == (1 if failure == "disabled_user" else 0)
        assert db.get(GoogleAdminBinding, 1).user_id is None


def test_missing_configuration_is_a_safe_unavailable_response(db_factory, tmp_path):
    settings = Settings(public_host="testserver", google_client_id_file=tmp_path / "absent",
                        google_client_secret_file=tmp_path / "absent", admin_google_email_file=tmp_path / "absent")
    with build_client(db_factory, settings=settings) as client:
        response = client.get("/api/v1/auth/google/start", follow_redirects=False)
        assert response.headers["location"] == "/?auth_error=unavailable"
        assert "absent" not in response.text + str(response.headers)
    with db_factory() as db:
        assert db.scalars(select(OAuthLoginAttempt)).all() == []


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(key, kid="test-kid"):
    return {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), "kid": kid, "alg": "RS256", "use": "sig"}


def claims(**changes):
    now = int(time.time())
    return {"iss": "https://accounts.google.com", "aud": "test-google-client-id", "exp": now + 3600,
            "iat": now, "sub": "test-subject", "nonce": "test-nonce", "email": "test@gmail.com",
            "email_verified": True, **changes}


def signed_token(key, values=None, kid="test-kid"):
    return jwt.encode(values or claims(), key, algorithm="RS256", headers={"kid": kid})


def verifier(key, requests=None):
    def handler(request):
        assert str(request.url) == GOOGLE_JWKS_URL
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, json={"keys": [public_jwk(key)]})
    return GoogleOidcClient(httpx.MockTransport(handler))


def test_valid_rs256_signature_and_jwks_cache(signing_key):
    requests = []
    client = verifier(signing_key, requests)
    for _ in range(2):
        identity = client.verify_id_token(signed_token(signing_key), "test-google-client-id", hash_token("test-nonce"))
        assert identity == GoogleIdentity("test-subject", "test@gmail.com", True)
    assert len(requests) == 1


@pytest.mark.parametrize("changes", [
    {"iss": "https://attacker.invalid"}, {"aud": "other-client"}, {"exp": 1},
    {"iat": 9999999999}, {"nonce": "wrong"}, {"sub": ""}, {"email": "invalid"},
    {"email_verified": False}, {"email_verified": "true"}, {"exp": "9999999999"},
    {"azp": "other-client"}, {"aud": ["test-google-client-id", "other-client"]},
])
def test_invalid_signed_claims_are_rejected(signing_key, changes):
    with pytest.raises(OidcRejected):
        verifier(signing_key).verify_id_token(signed_token(signing_key, claims(**changes)),
                                             "test-google-client-id", hash_token("test-nonce"))


@pytest.mark.parametrize("missing", ["iss", "aud", "exp", "iat", "sub", "nonce", "email", "email_verified"])
def test_missing_required_claim_is_rejected(signing_key, missing):
    values = claims()
    del values[missing]
    with pytest.raises(OidcRejected):
        verifier(signing_key).verify_id_token(signed_token(signing_key, values),
                                             "test-google-client-id", hash_token("test-nonce"))


@pytest.mark.parametrize("invalid", ["signature", "none", "hs256", "kid", "malformed"])
def test_invalid_signature_algorithm_or_key_is_rejected(signing_key, invalid):
    if invalid == "signature":
        token = signed_token(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    elif invalid == "none":
        token = jwt.encode(claims(), "", algorithm="none", headers={"kid": "test-kid"})
    elif invalid == "hs256":
        token = jwt.encode(claims(), "test-only-hmac-secret-for-security-test", algorithm="HS256", headers={"kid": "test-kid"})
    elif invalid == "kid":
        token = signed_token(signing_key, kid="unknown-kid")
    else:
        token = "not-a-jwt"
    with pytest.raises(OidcRejected):
        verifier(signing_key).verify_id_token(token, "test-google-client-id", hash_token("test-nonce"))


def test_exchange_and_callback_use_real_signature_verification_without_persisting_tokens(db_factory, signing_key, caplog):
    nonce = None
    requests = []
    token = None
    def handler(request):
        nonlocal token
        requests.append(request)
        if str(request.url) == GOOGLE_JWKS_URL:
            return httpx.Response(200, json={"keys": [public_jwk(signing_key)]})
        assert str(request.url) == GOOGLE_TOKEN_URL and request.method == "POST"
        body = parse_qs(request.content.decode())
        assert body["client_secret"] == ["test-only-google-client-secret"]
        assert body["grant_type"] == ["authorization_code"]
        assert body["redirect_uri"] == ["https://testserver/api/v1/auth/google/callback"]
        token = signed_token(signing_key, claims(nonce=nonce, email="operator@gmail.com", sub="operator-sub"))
        return httpx.Response(200, json={"id_token": token, "access_token": "test-discard-access-token",
                                       "refresh_token": "test-discard-refresh-token"})
    provider = GoogleOidcClient(httpx.MockTransport(handler))
    with build_client(db_factory, oidc_client=provider) as client:
        _, params = start(client)
        nonce = params["nonce"]
        response = callback(client, params["state"])
        assert response.headers["location"] == "/"
        session = client.get("/api/v1/auth/session").json()
        assert session["email"] == "operator@gmail.com" and session["role"] == "ADMIN"
        assert token not in response.text + str(response.headers) + caplog.text
        assert "test-discard-access-token" not in json.dumps(session) + caplog.text
    assert len(requests) == 2
    from privacy_helpers import assert_database_excludes
    assert_database_excludes(db_factory, [token, "test-discard-access-token",
        "test-discard-refresh-token", "test-only-google-client-secret"])
    with db_factory() as db:
        assert db.scalars(select(OAuthLoginAttempt)).all() == []
        assert db.scalar(select(User)).google_sub == "operator-sub"
        assert len(db.scalars(select(UserSession)).all()) == 1


@pytest.mark.parametrize("failure", ["timeout", "redirect", "bad_json", "token_missing"])
def test_provider_failure_never_exposes_response_or_follows_redirects(failure):
    requests = []
    def handler(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("test-provider-sensitive-message", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://attacker.invalid"})
        if failure == "bad_json":
            return httpx.Response(200, text="test-sensitive-provider-body")
        return httpx.Response(200, json={"access_token": "test-sensitive-token"})
    with pytest.raises(OidcRejected) as rejected:
        GoogleOidcClient(httpx.MockTransport(handler)).exchange(TestSettings(), "test-code", hash_token("test-nonce"))
    assert str(rejected.value) == "" and len(requests) == 1


@pytest.mark.parametrize("values,authoritative", [
    ({"email": "test@gmail.com"}, True),
    ({"email": "test@example.invalid", "hd": "example.invalid"}, True),
    ({"email": "test@example.invalid"}, False),
    ({"email": "test@example.invalid", "hd": "other.invalid"}, False),
])
def test_email_authority_for_admin_bootstrap(signing_key, values, authoritative):
    identity = verifier(signing_key).verify_id_token(signed_token(signing_key, claims(**values)),
                                                    "test-google-client-id", hash_token("test-nonce"))
    assert identity.email_authoritative == authoritative


def test_config_reads_protected_inputs_normalizes_email_and_ignores_request_origin(tmp_path, db_factory):
    files = {}
    for name, value in {"google_client_id": "test-google-client-id", "google_client_secret": "test-only-client-secret",
                        "admin_google_email": "OPERATOR@GMAIL.COM"}.items():
        path = tmp_path / name
        path.write_text(value)
        path.chmod(0o600)
        files[name + "_file"] = path
    settings = Settings(public_host="testserver", **files)
    assert settings.admin_google_email == "operator@gmail.com"
    with build_client(db_factory, settings=settings) as client:
        response = client.get("/api/v1/auth/google/start?redirect_uri=https://attacker.invalid", follow_redirects=False,
                              headers={"X-Forwarded-Host": "attacker.invalid"})
        params = parse_qs(urlsplit(response.headers["location"]).query)
        assert params["redirect_uri"] == ["https://testserver/api/v1/auth/google/callback"]
