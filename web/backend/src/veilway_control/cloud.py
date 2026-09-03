from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Protocol

import boto3
import httpx
from botocore.config import Config as AwsConfig
from botocore.exceptions import BotoCoreError, ClientError

from .config import Settings
from .models import VpnVm


class CloudMutationError(RuntimeError):
    def __init__(self, code: str, *, ambiguous: bool) -> None:
        super().__init__(code)
        self.code = code
        self.ambiguous = ambiguous


class CloudOperationError(RuntimeError):
    def __init__(self, code: str, *, definitive: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.definitive = definitive


@dataclass(frozen=True)
class RebootReceipt:
    request_id: str


class CloudProvider(Protocol):
    def reboot(self, vm: VpnVm) -> RebootReceipt: ...

    def operation_complete(self, request_id: str) -> bool: ...


class AwsCloudProvider:
    def __init__(self, settings: Settings, region: str) -> None:
        self.client = boto3.client(
            "ec2",
            region_name=region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=AwsConfig(
                connect_timeout=5,
                read_timeout=10,
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        )

    def reboot(self, vm: VpnVm) -> RebootReceipt:
        try:
            response = self.client.reboot_instances(InstanceIds=[vm.instance_id])
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "aws_rejected")
            raise CloudMutationError(str(code)[:64], ambiguous=False) from error
        except BotoCoreError as error:
            raise CloudMutationError("aws_transport_error", ambiguous=True) from error
        request_id = response.get("ResponseMetadata", {}).get("RequestId")
        if not isinstance(request_id, str) or not request_id:
            raise CloudMutationError("aws_missing_request_id", ambiguous=True)
        return RebootReceipt(request_id=request_id)

    def operation_complete(self, request_id: str) -> bool:
        del request_id
        return True


class YandexCloudProvider:
    def __init__(self, settings: Settings, instance_id: str) -> None:
        self.settings = settings
        self.instance_id = instance_id
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _iam_token(self) -> str:
        if self._token is not None and time.monotonic() < self._token_expires_at:
            return self._token
        try:
            response = httpx.get(
                self.settings.yandex_metadata_url,
                headers={"Metadata-Flavor": "Google"},
                timeout=5,
                follow_redirects=False,
                trust_env=False,
            )
            response.raise_for_status()
            payload = response.json()
            token = payload.get("access_token")
            expires_in = payload.get("expires_in", 300)
        except (httpx.HTTPError, ValueError) as error:
            raise CloudMutationError("yandex_token_error", ambiguous=False) from error
        if not isinstance(token, str) or not token:
            raise CloudMutationError("yandex_token_error", ambiguous=False)
        self._token = token
        lifetime = expires_in if isinstance(expires_in, int) else 300
        self._token_expires_at = time.monotonic() + max(1, lifetime - 60)
        return token

    def reboot(self, vm: VpnVm) -> RebootReceipt:
        url = f"{self.settings.yandex_compute_api}/compute/v1/instances/{vm.instance_id}:restart"
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {self._iam_token()}"},
                json={},
                timeout=15,
                follow_redirects=False,
                trust_env=False,
            )
        except httpx.TransportError as error:
            raise CloudMutationError("yandex_transport_error", ambiguous=True) from error
        if response.status_code >= 400:
            raise CloudMutationError(
                f"yandex_http_{response.status_code}",
                ambiguous=response.status_code >= 500
                or response.status_code in {408, 429},
            )
        try:
            request_id = response.json()["id"]
        except (KeyError, TypeError, ValueError) as error:
            raise CloudMutationError("yandex_missing_operation_id", ambiguous=True) from error
        if not isinstance(request_id, str) or not request_id:
            raise CloudMutationError("yandex_missing_operation_id", ambiguous=True)
        return RebootReceipt(request_id=request_id)

    def operation_complete(self, request_id: str) -> bool:
        url = (
            f"{self.settings.yandex_compute_api}/compute/v1/instances/"
            f"{self.instance_id}/operations"
        )
        try:
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {self._iam_token()}"},
                params={"pageSize": 100},
                timeout=10,
                follow_redirects=False,
                trust_env=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise CloudOperationError("yandex_operation_unavailable") from error
        operations = payload.get("operations")
        if not isinstance(operations, list):
            raise CloudOperationError("yandex_operation_unavailable")
        operation = next(
            (
                candidate
                for candidate in operations
                if isinstance(candidate, dict) and candidate.get("id") == request_id
            ),
            None,
        )
        if operation is None:
            return False
        if operation.get("error"):
            raise CloudOperationError("yandex_operation_failed", definitive=True)
        return operation.get("done") is True


def provider_for(settings: Settings, vm: VpnVm) -> CloudProvider:
    if vm.slug == "aws-direct" and vm.provider == "aws":
        return AwsCloudProvider(settings, vm.region)
    if vm.slug == "yc-direct" and vm.provider == "yandex":
        return YandexCloudProvider(settings, vm.instance_id)
    raise CloudMutationError("unsupported_provider", ambiguous=False)
