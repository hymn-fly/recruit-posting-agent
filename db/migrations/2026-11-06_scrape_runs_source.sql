-- scrape_runs의 키를 company_id → source(크롤링한 사이트 링크)로 바꾼다.
-- 기존 행은 회사 portal_url을 source로 채운다. 여러 번 실행해도 안전하다.
BEGIN;

-- 1. source 컬럼 추가 후 기존 행을 회사 portal_url로 채움
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS source TEXT;

-- company_id가 이미 지워진 상태(재실행)면 채울 것이 없으므로 건너뜀
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'scrape_runs' AND column_name = 'company_id') THEN
        UPDATE scrape_runs s
           SET source = c.portal_url
          FROM companies c
         WHERE s.source IS NULL AND s.company_id = c.id;
    END IF;
END $$;

ALTER TABLE scrape_runs ALTER COLUMN source SET NOT NULL;

-- 2. company_id 키와 인덱스 제거
DROP INDEX IF EXISTS idx_scrape_runs_company;
ALTER TABLE scrape_runs DROP COLUMN IF EXISTS company_id;

-- 3. 사이트별 최근 상태 조회용 인덱스와 주석
CREATE INDEX IF NOT EXISTS idx_scrape_runs_source_run_at ON scrape_runs(source, run_at DESC);
COMMENT ON TABLE  scrape_runs IS '사이트별 크롤링 실행 기록. "자동 확인 불가" 사이트를 추적하는 용도';
COMMENT ON COLUMN scrape_runs.source IS '크롤링한 사이트: 회사 채용 포털이면 companies.portal_url, 채용 플랫폼이면 사이트 링크(예: https://www.saramin.co.kr)';

COMMIT;
