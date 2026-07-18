"""비밀값 조회 포트. 구현은 env 또는 macOS Keychain 뒤에 격리한다 (PRD 7.3)."""

from __future__ import annotations

from typing import Protocol


class SecretProvider(Protocol):
    def get_secret(self, name: str) -> str | None:
        """비밀값을 조회한다. 조회 즉시 마스킹 레지스트리에 등록해야 한다."""
        ...
