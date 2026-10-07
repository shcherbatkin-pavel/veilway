"""Google's confidential-client OIDC flow; JWT verification uses PyJWT."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hmac
import re
import threading
import time
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import ensure_active_user
from .config import Settings
from .models import GoogleAdminBinding, User
from .security import hash_token


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class OidcRejected(Exception):
    """Intentionally contains no provider response, code, token or identity."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    email_authoritative: bool


class GoogleOidcClient:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_expire = 0.0
        self._last_refresh = float("-inf")
        self._lock = threading.Lock()

    @staticmethod
    def authorization_url(settings: Settings, state: str, nonce: str) -> str:
        return GOOGLE_AUTH_URL + "?" + urlencode({
            "client_id": settings.google_client_id,
            "response_type": "code",
            "scope": "openid email",
            "redirect_uri": settings.google_redirect_uri,
            "state": state,
            "nonce": nonce,
            "prompt": "select_account",
        })

    def _json(self, method: str, url: str, **kwargs) -> dict:
        try:
            with httpx.Client(
                transport=self.transport, timeout=10, follow_redirects=False, trust_env=False,
            ) as client:
                response = client.request(method, url, **kwargs)
            response.raise_for_status()
            if len(response.content) > 131072:
                raise OidcRejected()
            document = response.json()
            if not isinstance(document, dict):
                raise OidcRejected()
            return document
        except (httpx.HTTPError, ValueError):
            raise OidcRejected() from None

    def _key(self, kid: str) -> jwt.PyJWK:
        # Fixed HTTPS endpoint; JWT-supplied jku/x5u URLs are never followed.
        with self._lock:
            now = time.monotonic()
            if now >= self._keys_expire or (kid not in self._keys and now - self._last_refresh >= 30):
                self._last_refresh = now
                document = self._json("GET", GOOGLE_JWKS_URL)
                keys = document.get("keys")
                if not isinstance(keys, list) or not 1 <= len(keys) <= 32:
                    raise OidcRejected()
                try:
                    parsed = {
                        item["kid"]: jwt.PyJWK.from_dict(item, algorithm="RS256")
                        for item in keys
                        if isinstance(item, dict) and item.get("kty") == "RSA"
                        and item.get("use", "sig") == "sig" and item.get("alg", "RS256") == "RS256"
                    }
                    if not parsed or any(not isinstance(key, str) for key in parsed):
                        raise OidcRejected()
                except (KeyError, ValueError, jwt.PyJWTError):
                    raise OidcRejected() from None
                self._keys = parsed
                self._keys_expire = now + 300
            key = self._keys.get(kid)
            if key is None:
                raise OidcRejected()
            return key

    def verify_id_token(self, token: str, client_id: str, nonce_hash: bytes) -> GoogleIdentity:
        try:
            if not isinstance(token, str) or len(token) > 16384:
                raise OidcRejected()
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 128:
                raise OidcRejected()
            claims = jwt.decode(
                token, self._key(kid).key, algorithms=["RS256"],
                audience=client_id, issuer=GOOGLE_ISSUERS,
                options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce", "email", "email_verified"]},
            )
            if type(claims["exp"]) is not int or type(claims["iat"]) is not int:
                raise OidcRejected()
            audience = claims["aud"]
            if isinstance(audience, list) and len(audience) > 1 and "azp" not in claims:
                raise OidcRejected()
            if "azp" in claims and claims["azp"] != client_id:
                raise OidcRejected()
            nonce = claims["nonce"]
            if not isinstance(nonce, str) or not hmac.compare_digest(hash_token(nonce), nonce_hash):
                raise OidcRejected()
            subject, email = claims["sub"], claims["email"]
            if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
                raise OidcRejected()
            if not isinstance(email, str) or len(email) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise OidcRejected()
            if claims["email_verified"] is not True:
                raise OidcRejected()
            email = email.lower()
            domain = email.rsplit("@", 1)[1]
            hosted_domain = claims.get("hd")
            authoritative = domain == "gmail.com" or (
                isinstance(hosted_domain, str) and hosted_domain.lower() == domain
            )
            return GoogleIdentity(subject, email, authoritative)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise OidcRejected() from None

    def exchange(self, settings: Settings, code: str, nonce_hash: bytes) -> GoogleIdentity:
        document = self._json("POST", GOOGLE_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "code": code,
        })
        token = document.get("id_token")
        if not isinstance(token, str):
            raise OidcRejected()
        # Access/refresh tokens, if returned, are discarded with this response.
        return self.verify_id_token(token, settings.google_client_id, nonce_hash)


@lru_cache(maxsize=1)
def get_google_client() -> GoogleOidcClient:
    return GoogleOidcClient()


def resolve_google_user(db: Session, identity: GoogleIdentity, admin_email: str) -> User:
    # The migration creates this row before any login. Lock even before the
    # first binding so simultaneous callbacks cannot appoint two admins.
    binding = db.scalar(select(GoogleAdminBinding).where(GoogleAdminBinding.id == 1).with_for_update())
    if binding is None:
        raise OidcRejected()
    user = db.scalar(select(User).where(User.google_sub == identity.subject))
    if user is None:
        user = User(google_sub=identity.subject, email=identity.email, role="USER")
        db.add(user)
        db.flush()
    ensure_active_user(user)
    if binding.user_id is None and identity.email == admin_email:
        # Google is authoritative for Gmail/Workspace addresses, not every
        # third-party email attached to an otherwise valid Google account.
        if not identity.email_authoritative:
            raise OidcRejected()
        binding.user_id = user.id
    user.email = identity.email
    user.role = "ADMIN" if binding.user_id == user.id else "USER"
    return user
