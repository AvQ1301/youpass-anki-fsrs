PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS exercises (
    exercise_id TEXT PRIMARY KEY,
    skill TEXT NOT NULL DEFAULT 'reading',
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    passage_number INTEGER CHECK (passage_number BETWEEN 1 AND 3 OR passage_number IS NULL),
    section_number INTEGER CHECK (section_number BETWEEN 1 AND 4 OR section_number IS NULL),
    part_number INTEGER CHECK (part_number BETWEEN 1 AND 3 OR part_number IS NULL),
    question_types TEXT NOT NULL DEFAULT '[]',
    question_count INTEGER CHECK (question_count >= 0 OR question_count IS NULL),
    completion_status TEXT NOT NULL DEFAULT 'unknown',
    last_score INTEGER,
    last_total INTEGER,
    best_score INTEGER,
    best_total INTEGER,
    last_band REAL,
    best_band REAL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    anki_note_id INTEGER UNIQUE,
    anki_card_id INTEGER UNIQUE,
    source_updated_at TEXT,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_sync_error TEXT
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    exercise_id TEXT NOT NULL REFERENCES exercises(exercise_id) ON DELETE CASCADE,
    score_kind TEXT NOT NULL DEFAULT 'accuracy',
    correct INTEGER,
    total INTEGER,
    band REAL,
    accuracy REAL NOT NULL,
    attempted_at TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
    review_status TEXT NOT NULL DEFAULT 'pending',
    anki_review_id TEXT,
    review_error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_exercise ON attempts(exercise_id, attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_attempts_review_status ON attempts(review_status);

CREATE TABLE IF NOT EXISTS error_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    exercise_id TEXT NOT NULL REFERENCES exercises(exercise_id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('question_error', 'criterion_score')),
    item_key TEXT,
    category TEXT,
    outcome TEXT NOT NULL,
    selected_answer TEXT,
    correct_answer TEXT,
    score REAL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(attempt_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_error_observations_exercise ON error_observations(exercise_id);
CREATE INDEX IF NOT EXISTS idx_error_observations_category ON error_observations(kind, category);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    discovered INTEGER NOT NULL DEFAULT 0,
    synchronized INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crawl_checkpoints (
    scope TEXT PRIMARY KEY,
    next_page INTEGER NOT NULL DEFAULT 1,
    total_pages INTEGER,
    updated_at TEXT NOT NULL
);
