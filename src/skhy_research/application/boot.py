"""애플리케이션 부팅 시 안전장치를 초기화한다 (P0-03).

broker_mode!=paper 차단은 `config.load_settings()`에서 이미 수행된다. 여기서는
로그 마스킹 필터를 설치하고, 부팅 로그 자체에도 마스킹이 적용됨을 보장한다.
"""

from __future__ import annotations

import logging

from skhy_research.application.config import Settings
from skhy_research.observability.masking import install_masking_filter

logger = logging.getLogger("skhy_research.boot")


def bootstrap(settings: Settings) -> None:
    install_masking_filter()
    logger.info(
        "boot: env=%s broker_mode=%s config_hash=%s",
        settings.env_name,
        settings.broker_mode,
        settings.config_hash,
    )
