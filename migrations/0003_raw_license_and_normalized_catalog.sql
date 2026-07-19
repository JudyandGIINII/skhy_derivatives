-- P0-12 보강: raw 레코드에 수집 시점의 이용조건·provider catalog version을
-- 동결하고, lineage child가 실제 normalized 레코드를 가리키도록 catalog를 추가한다.

ALTER TABLE raw_record_catalog
    ADD COLUMN IF NOT EXISTS license_terms JSONB,
    ADD COLUMN IF NOT EXISTS provider_catalog_version VARCHAR;

-- 기존 raw 행은 당시 catalog snapshot을 복원할 수 없으므로 명시적인 legacy 값으로
-- 표시한다. 허용 여부는 안전한 기본값(false)으로 둔다.
UPDATE raw_record_catalog
SET license_terms = jsonb_build_object(
        'license_terms_url', 'UNAVAILABLE_LEGACY_RECORD',
        'storage_redistribution_allowed', false
    )
WHERE license_terms IS NULL;

UPDATE raw_record_catalog
SET provider_catalog_version = 'legacy-unversioned'
WHERE provider_catalog_version IS NULL;

ALTER TABLE raw_record_catalog
    ALTER COLUMN license_terms SET NOT NULL,
    ALTER COLUMN provider_catalog_version SET NOT NULL;

CREATE TABLE IF NOT EXISTS normalized_record_catalog (
    normalized_record_id VARCHAR PRIMARY KEY,
    record_type          VARCHAR NOT NULL,
    payload              JSONB NOT NULL,
    created_at_utc       BIGINT NOT NULL
);
