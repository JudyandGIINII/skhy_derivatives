"""P0-12: Phase 0 완료조건 통합 검증 (`implementation_plan.md` 5.2).

Phase 0 완료조건 4가지 중 코드로 검증 가능한 3가지를 여기서 자동 확인한다.
2번째 조건("사용자가 조회 전용 키를 주입한 환경에서 capability smoke 통과")은
실제 브로커 키가 있어야 하며, `tests/e2e/test_provider_smoke.py`
(`@pytest.mark.smoke`)에 스캐폴딩만 마련해 둔다 — 이 세션에는 실제 키가
없으므로 실행할 수 없고, 이는 알려진 한계로 문서화한다.
"""

from __future__ import annotations

import ast
import json
import time
import uuid
from pathlib import Path

import pytest

from skhy_research.adapters.persistence.manifest_store import (
    add_lineage_edge,
    trace_lineage_for_record,
)
from skhy_research.adapters.persistence.normalized_record_store import (
    get_normalized_record,
    save_normalized_record,
)
from skhy_research.adapters.persistence.raw_recorder import RawRecorder, compute_dedupe_key
from skhy_research.adapters.providers.fixture_registry import build_fixture_provider_registry
from skhy_research.application.capability_probe import run_capability_probe
from skhy_research.application.health_monitor import HealthMonitor
from skhy_research.application.provider_registry import (
    NonPaperBrokerRegistrationError,
    ProviderRegistry,
)
from skhy_research.data.normalization.market_quote_normalizer import normalize_market_quote
from skhy_research.domain.experiment import LineageEdge
from skhy_research.domain.market import MarketQuote

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "skhy_research"
_NOW = time.time_ns()


@pytest.mark.integration
def test_raw_record_is_traceable_to_normalized_record(clean_pg, tmp_path: Path) -> None:
    """raw에서 이용조건과 실제 normalized DB 레코드까지 양방향 추적됨을 실증한다."""
    recorder = RawRecorder(clean_pg, tmp_path)
    provider_registry = build_fixture_provider_registry()
    provider_catalog = provider_registry.get_market_data("kis").capabilities()
    raw_row = {
        "source": "kis",
        "venue": "KRX",
        "symbol": "000660",
        "event_time_utc": _NOW,
        "received_time_utc": _NOW + 1_000_000,
        "currency": "KRW",
        "session": "REGULAR",
        "is_delayed": False,
        "adjustment_status": "RAW",
        "instrument_id": "SKHY_000660_KRX_COMMON",
        "bid_price": "202900",
        "ask_price": "203000",
        "bid_size": "120",
        "ask_size": "95",
    }
    payload = json.dumps(raw_row).encode("utf-8")
    dedupe_key = compute_dedupe_key("kis", "quotes", "quote", _NOW, "n/a")

    stored = recorder.store(
        source="kis",
        dataset="quotes",
        payload=payload,
        received_at_utc=_NOW,
        collection_run_id="phase0-e2e",
        dedupe_key=dedupe_key,
        provider_catalog=provider_catalog,
    )

    # 정규화: raw payload -> MarketQuote. bid_price/ask_price는 문자열이지만 Decimal로 검증된다.
    normalized = normalize_market_quote("kis", "quotes", raw_row, raw_record_id=stored.meta.raw_record_id)
    normalized_record = save_normalized_record(clean_pg, normalized, created_at_utc=_NOW)

    add_lineage_edge(
        clean_pg,
        LineageEdge(
            edge_id=str(uuid.uuid4()),
            run_id="phase0-e2e",
            parent_record_id=stored.meta.raw_record_id,
            parent_layer="raw",
            child_record_id=normalized_record.normalized_record_id,
            child_layer="normalized",
            algorithm_version="market_quote_normalizer@1.0.0",
            created_at_utc=_NOW,
        ),
    )

    # 실제 normalized DB 레코드 ID에서 lineage parent를 찾고 raw catalog까지 역조회한다.
    loaded_normalized = get_normalized_record(clean_pg, normalized_record.normalized_record_id)
    assert loaded_normalized is not None
    persisted_quote = MarketQuote.model_validate(loaded_normalized.payload)
    edges = trace_lineage_for_record(
        clean_pg, "phase0-e2e", loaded_normalized.normalized_record_id
    )
    assert len(edges) == 1
    assert edges[0].parent_layer == "raw"
    traced_raw = recorder.get_meta(edges[0].parent_record_id)
    assert traced_raw is not None

    assert persisted_quote.instrument_id == "SKHY_000660_KRX_COMMON"
    assert traced_raw.source == "kis"
    assert traced_raw.received_at_utc == _NOW
    assert traced_raw.payload_checksum == stored.meta.payload_checksum
    assert traced_raw.license_terms.license_terms_url == provider_catalog.license_terms_url
    assert (
        traced_raw.license_terms.storage_redistribution_allowed
        == provider_catalog.storage_redistribution_allowed
    )
    assert traced_raw.provider_catalog_version == provider_catalog.catalog_version


def test_fixture_registry_capability_probe_and_health_recording_succeed() -> None:
    registry = build_fixture_provider_registry()
    results = run_capability_probe(registry)
    assert all(r.ok for r in results)

    monitor = HealthMonitor()
    for result in results:
        assert result.entry is not None
        monitor.record_event(result.port_type, result.provider_name, _NOW, latency_ms=0.0)
    snapshot = monitor.snapshot()
    assert len(snapshot) == len(results)
    assert all(state.is_connected for state in snapshot.values())


def test_broker_registry_rejects_any_non_paper_broker_name() -> None:
    registry = ProviderRegistry()

    class _StubBroker:
        def capabilities(self):  # noqa: ANN201
            raise NotImplementedError

        def account_snapshot(self):  # noqa: ANN201
            raise NotImplementedError

        def submit_order(self, order):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def cancel_order(self, order_id):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def poll_fills(self, order_id):  # noqa: ANN001, ANN201
            raise NotImplementedError

    for bad_name in ("kis", "toss", "live", "real"):
        with pytest.raises(NonPaperBrokerRegistrationError):
            registry.register_broker(bad_name, _StubBroker())


def test_no_real_broker_order_submission_exists_in_source_tree() -> None:
    """소스 트리 전체에서 'paper' 외의 브로커가 submit_order를 구현하지 않았는지 정적으로 확인한다."""
    offending: list[str] = []
    for py_file in _SRC_ROOT.rglob("*.py"):
        if "paper" in py_file.parts:
            continue  # PaperBrokerProvider 자신은 예외
        if py_file.name in {"broker.py"} and "ports" in py_file.parts:
            continue  # Protocol 정의 자체는 예외
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "submit_order":
                offending.append(str(py_file.relative_to(_REPO_ROOT)))
    assert offending == [], f"paper 외 위치에서 submit_order 구현 발견: {offending}"
