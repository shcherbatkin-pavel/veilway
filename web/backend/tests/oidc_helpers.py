"""Synthetic settings/provider used only by local tests."""

import hmac

from veilway_control.config import Settings
from veilway_control.oidc import GoogleIdentity, GoogleOidcClient, OidcRejected
from veilway_control.security import hash_token


class TestSettings(Settings):
    __test__ = False

    @property
    def google_client_id(self):
        return "test-google-client-id"

    @property
    def google_client_secret(self):
        return "test-only-google-client-secret"

    @property
    def admin_google_email(self):
        return "operator@gmail.com"


class FakeGoogleClient(GoogleOidcClient):
    def __init__(self, identity=None):
        self.identity = identity or GoogleIdentity("operator-sub", "operator@gmail.com", True)
        self.nonce = None
        self.exchanges = 0
        self.rejected = False

    def authorization_url(self, settings, state, nonce):
        self.nonce = nonce
        return super().authorization_url(settings, state, nonce)

    def exchange(self, settings, code, nonce_hash):
        self.exchanges += 1
        if self.rejected or self.nonce is None or not hmac.compare_digest(hash_token(self.nonce), nonce_hash):
            raise OidcRejected()
        return self.identity
