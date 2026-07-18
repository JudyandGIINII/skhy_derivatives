"""P0-03: env/Keychain SecretProvider가 조회 즉시 마스킹 레지스트리에 등록하는지 검증한다."""

from __future__ import annotations

import pytest

from skhy_research.adapters.secrets.env_secret_provider import EnvSecretProvider
from skhy_research.adapters.secrets.factory import build_secret_provider
from skhy_research.adapters.secrets.keychain_secret_provider import KeychainSecretProvider
from skhy_research.observability.masking import clear_registered_secrets, mask


@pytest.fixture(autouse=True)
def _isolated_secret_registry():
    clear_registered_secrets()
    yield
    clear_registered_secrets()


def test_env_secret_provider_registers_value_for_masking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANARY_TEST_SECRET", "canary-env-value-777")
    provider = EnvSecretProvider()

    value = provider.get_secret("CANARY_TEST_SECRET")

    assert value == "canary-env-value-777"
    assert "canary-env-value-777" not in mask(f"leaked: {value}")


def test_env_secret_provider_missing_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_CANARY_SECRET", raising=False)
    provider = EnvSecretProvider()

    assert provider.get_secret("MISSING_CANARY_SECRET") is None


def test_keychain_secret_provider_registers_value_for_masking(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_password(service_name: str, name: str) -> str | None:
        assert service_name == "skhy-research"
        assert name == "KIS_APP_SECRET"
        return "canary-keychain-value-321"

    monkeypatch.setattr(
        "skhy_research.adapters.secrets.keychain_secret_provider.keyring.get_password",
        fake_get_password,
    )
    provider = KeychainSecretProvider()

    value = provider.get_secret("KIS_APP_SECRET")

    assert value == "canary-keychain-value-321"
    assert "canary-keychain-value-321" not in mask(f"leaked: {value}")


def test_factory_selects_env_backend_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SKHY_SECRET_BACKEND", raising=False)
    provider = build_secret_provider()
    assert isinstance(provider, EnvSecretProvider)


def test_factory_selects_keychain_backend_explicitly() -> None:
    provider = build_secret_provider("keychain")
    assert isinstance(provider, KeychainSecretProvider)


def test_factory_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="SKHY_SECRET_BACKEND"):
        build_secret_provider("unknown-backend")
