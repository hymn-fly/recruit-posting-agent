CREATE TABLE IF NOT EXISTS posting_gap_reports (
    posting_id INTEGER PRIMARY KEY REFERENCES job_postings(id) ON DELETE CASCADE,
    report_md  TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE posting_gap_reports IS '공고별 내 역량 분석 결과. 공고당 최신 1건. 프로필 문서가 바뀌면 전체 삭제';
