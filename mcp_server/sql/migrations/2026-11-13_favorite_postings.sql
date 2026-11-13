CREATE TABLE IF NOT EXISTS favorite_postings (
    posting_id INTEGER PRIMARY KEY REFERENCES job_postings(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_favorite_postings_created_at ON favorite_postings(created_at DESC);
