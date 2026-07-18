"""P0-12 완료조건의 2번째 항목: 사용자 키 주입 환경의 조회 전용 smoke test.

이 파일은 스캐폴딩만 제공한다. 실제 KRX/KIS/Toss 조회 전용 어댑터는 아직
구현되지 않았다(G-02 capability probe 결과 확인 후 Phase 1에서 실제 provider
어댑터를 구현할 예정). 현재는 필요한 환경변수가 없으면 명시적으로 skip한다.

실행: `SKHY_ENV=smoke uv run pytest -m smoke tests/e2e/test_provider_smoke.py -v`
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.smoke

_REQUIRED_ENV_VARS = ("KRX_API_KEY", "KIS_APP_KEY", "KIS_APP_SECRET", "TOSS_CLIENT_ID")


def _missing_env_vars() -> list[str]:
    return [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name)]


def test_krx_kis_toss_smoke_requires_real_keys() -> None:
    missing = _missing_env_vars()
    if missing:
        pytest.skip(
            "실제 조회 전용 키가 없어 smoke test를 건너뜀 "
            f"(누락: {missing}). G-02 해소 및 Phase 1 실제 provider 어댑터 구현 후 재실행."
        )
    pytest.fail(
        "실제 provider 어댑터가 아직 구현되지 않았다 (Phase 1 예정). "
        "키는 주입됐지만 검증할 실제 어댑터가 없어 smoke test를 완료할 수 없다."
    )
