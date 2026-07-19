"""macOS Keychain 기반 SecretProvider.

`keyring` 라이브러리로 시스템 Keychain에서 조회한다. 값 저장은 이 코드의 책임이
아니며, 운영자가 `keyring set skhy-research <NAME>`으로 사전에 등록해야 한다.
"""

from __future__ import annotations

import keyring

from skhy_research.observability.masking import register_secret

_DEFAULT_SERVICE_NAME = "skhy-research"


class KeychainSecretProvider:
    def __init__(self, service_name: str = _DEFAULT_SERVICE_NAME) -> None:
        self._service_name = service_name

    def get_secret(self, name: str) -> str | None:
        value = keyring.get_password(self._service_name, name)
        register_secret(value)
        return value
