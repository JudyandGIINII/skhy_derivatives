"""SKHY_SECRET_BACKEND(env|keychain)에 따라 SecretProvider 구현을 선택한다."""

from __future__ import annotations

import os

from skhy_research.adapters.secrets.env_secret_provider import EnvSecretProvider
from skhy_research.adapters.secrets.keychain_secret_provider import KeychainSecretProvider
from skhy_research.ports.secrets import SecretProvider

_BACKENDS = {"env", "keychain"}


def build_secret_provider(backend: str | None = None) -> SecretProvider:
    resolved = backend or os.environ.get("SKHY_SECRET_BACKEND", "env")
    if resolved not in _BACKENDS:
        raise ValueError(
            f"지원하지 않는 SKHY_SECRET_BACKEND='{resolved}'. 허용값: {sorted(_BACKENDS)}"
        )
    if resolved == "env":
        return EnvSecretProvider()
    return KeychainSecretProvider()
