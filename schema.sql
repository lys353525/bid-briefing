-- pSEO 엔진 SQLite 스키마
-- 원시 수집(raw) → 가공 지표(metric) → 발행 글(page) → 품질 검증(quality) 4계층.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 1) 실행 이력 ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    mode         TEXT NOT NULL,              -- live | sim
    as_of        TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end   TEXT NOT NULL,
    collected    INTEGER NOT NULL DEFAULT 0,
    computed     INTEGER NOT NULL DEFAULT 0,
    published    INTEGER NOT NULL DEFAULT 0,
    held         INTEGER NOT NULL DEFAULT 0,
    skipped      INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'running',   -- running | ok | failed
    note         TEXT
);

-- 2) 원시 데이터 ----------------------------------------------------------
-- key_hash  : 자연키(bidNtceNo-bidNtceOrd)의 sha256 → 중복 수집 차단
-- payload_hash: 내용 해시 → 같은 공고의 정정/변경 감지
CREATE TABLE IF NOT EXISTS raw_items (
    key_hash      TEXT PRIMARY KEY,
    natural_key   TEXT NOT NULL,
    source        TEXT NOT NULL,
    biz_type      TEXT,                      -- 공사 | 용역 | 물품
    payload       TEXT NOT NULL,             -- 원본 JSON 문자열
    payload_hash  TEXT NOT NULL,
    revision      INTEGER NOT NULL DEFAULT 1,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    run_id        TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_items_last_seen ON raw_items(last_seen);
CREATE INDEX IF NOT EXISTS idx_raw_items_run ON raw_items(run_id);

-- 3) 가공 지표 스냅샷 ----------------------------------------------------
-- scope_type: national | region | field | top
CREATE TABLE IF NOT EXISTS metric_snapshots (
    snapshot_id  TEXT PRIMARY KEY,           -- scope_type|scope_value|window_end
    run_id       TEXT,
    scope_type   TEXT NOT NULL,
    scope_value  TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end   TEXT NOT NULL,
    facts        TEXT NOT NULL,              -- 계산·표시 문자열까지 포함한 JSON
    computed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metric_scope ON metric_snapshots(scope_type, scope_value, window_end);

-- 4) 발행 글 --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pages (
    page_key           TEXT PRIMARY KEY,     -- scope_type|scope_value|window_end
    slug               TEXT NOT NULL UNIQUE,
    title              TEXT NOT NULL,
    description        TEXT NOT NULL,
    category           TEXT NOT NULL,
    scope_type         TEXT NOT NULL,
    scope_value        TEXT NOT NULL,
    window_start       TEXT NOT NULL,
    window_end         TEXT NOT NULL,
    content_hash       TEXT,
    body_chars         INTEGER,
    quality_score      REAL,
    status             TEXT NOT NULL,        -- published | held | unchanged
    file_path          TEXT,
    first_published_at TEXT,
    updated_at         TEXT NOT NULL,
    run_id             TEXT
);
CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status);
CREATE INDEX IF NOT EXISTS idx_pages_window ON pages(window_end);

-- 5) 품질 검증 결과 ------------------------------------------------------
CREATE TABLE IF NOT EXISTS quality_checks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    page_key   TEXT NOT NULL,
    run_id     TEXT,
    passed     INTEGER NOT NULL,
    score      REAL,
    details    TEXT NOT NULL,                -- 검사별 결과 JSON
    checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quality_page ON quality_checks(page_key);

-- 6) 중복 판정용 shingle -------------------------------------------------
CREATE TABLE IF NOT EXISTS page_shingles (
    page_key TEXT NOT NULL,
    shingle  TEXT NOT NULL,
    PRIMARY KEY (page_key, shingle)
);

-- 7) 사건 기록 -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS incidents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT,
    level      TEXT NOT NULL,                -- info | warn | error
    code       TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
