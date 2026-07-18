-- P0-08 보강: concurrent collector가 동일 raw identity를 동시에 기록해도
-- catalog에는 canonical 행 하나만 남도록 DB 유일 제약을 추가한다.
--
-- 기존 중복을 자동 삭제하면 어느 payload를 canonical로 선택했는지 감사할 수 없으므로
-- 중복이 있으면 명시적으로 중단하고 운영자가 먼저 reconciliation하도록 한다.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM raw_record_catalog
        GROUP BY source, dataset, dedupe_key
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'raw_record_catalog에 (source, dataset, dedupe_key) 중복이 있어 유일 제약을 추가할 수 없습니다.'
            USING HINT = '중복 payload를 reconciliation한 뒤 0002 migration을 다시 실행하십시오.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_raw_record_source_dataset_dedupe'
          AND conrelid = 'raw_record_catalog'::regclass
    ) THEN
        ALTER TABLE raw_record_catalog
            ADD CONSTRAINT uq_raw_record_source_dataset_dedupe
            UNIQUE (source, dataset, dedupe_key);
    END IF;
END
$$;
