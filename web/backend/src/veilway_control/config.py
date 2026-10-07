from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VEILWAY_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    pki_socket_path: Path = Path("/run/veilway-pki/pki.sock")
    public_host: str = "veilway.ru"
    database_host: str = "db"
    database_port: int = 5432
    database_name: str = "veilway_control"
    database_user: str = "veilway_control"
    database_password_file: Path = Path("/run/secrets/postgres_password")
    session_hours: int = Field(default=8, ge=1, le=168)
    heartbeat_stale_seconds: int = Field(default=60, ge=30, le=600)
    restart_timeout_seconds: int = Field(default=900, ge=60, le=3600)
    worker_interval_seconds: int = Field(default=3, ge=1, le=30)
    session_cookie_name: Literal["__Host-veilway_session"] = "__Host-veilway_session"
    cookie_secure: bool = True
    google_client_id_file: Path = Path("/run/secrets/google_client_id")
    google_client_secret_file: Path = Path("/run/secrets/google_client_secret")
    admin_google_email_file: Path = Path("/run/secrets/admin_google_email")
    oauth_cookie_name: Literal["__Host-veilway_oauth"] = "__Host-veilway_oauth"
    oauth_attempt_seconds: int = Field(default=600, ge=60, le=600)
    aws_access_key_id_file: Path = Path("/run/secrets/aws_access_key_id")
    aws_secret_access_key_file: Path = Path("/run/secrets/aws_secret_access_key")
    yandex_metadata_url: str = (
        "http://169.254.169.254/computeMetadata/v1/instance/"
        "service-accounts/default/token"
    )
    yandex_compute_api: str = "https://compute.api.cloud.yandex.net"

    @field_validator("public_host")
    @classmethod
    def validate_public_host(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != "veilway.ru" and normalized not in {
            "localhost",
            "testserver",
        }:
            raise ValueError("public_host must be veilway.ru outside local tests")
        return normalized

    @staticmethod
    def read_secret(path: Path) -> str:
        try:
            parts = path.parts
            if parts[:4] == ("/", "proc", "self", "fd") and parts[-1].isdigit():
                value = os.pread(int(parts[-1]), 65536, 0).decode("utf-8").strip()
            else:
                value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as error:
            raise RuntimeError(f"required secret file is unavailable: {path}") from error
        if not value:
            raise RuntimeError(f"required secret file is empty: {path}")
        return value

    @property
    def database_password(self) -> str:
        return self.read_secret(self.database_password_file)

    @property
    def aws_access_key_id(self) -> str:
        return self.read_secret(self.aws_access_key_id_file)

    @property
    def aws_secret_access_key(self) -> str:
        return self.read_secret(self.aws_secret_access_key_file)

    @property
    def google_client_id(self) -> str:
        return self.read_secret(self.google_client_id_file)

    @property
    def google_client_secret(self) -> str:
        return self.read_secret(self.google_client_secret_file)

    @property
    def admin_google_email(self) -> str:
        value = self.read_secret(self.admin_google_email_file).lower()
        if len(value) > 320 or value.count("@") != 1 or any(c.isspace() for c in value):
            raise RuntimeError("invalid configured administrator email")
        local, domain = value.split("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise RuntimeError("invalid configured administrator email")
        return value

    @property
    def google_redirect_uri(self) -> str:
        # Fixed origin and path: never derive an OAuth redirect from request headers.
        return f"https://{self.public_host}/api/v1/auth/google/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
