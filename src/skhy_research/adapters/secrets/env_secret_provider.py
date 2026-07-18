"""환경변수 기반 SecretProvider (기본 backend)."""

from __future__ import annotations

import os

from skhy_research.observability.masking import register_secret


class EnvSecretProvider:
    def get_secret(self, name: str) -> str | None:
        value = os.environ.get(name)
        register_secret(value)
        return value
