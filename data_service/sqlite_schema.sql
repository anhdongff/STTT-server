PRAGMA foreign_keys = ON;

-- =========================
-- users
-- =========================

CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,

  email TEXT UNIQUE,
  display_name TEXT,
  password_hash TEXT NOT NULL,

  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER trg_users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW
BEGIN
  UPDATE users
  SET updated_at = CURRENT_TIMESTAMP
  WHERE id = OLD.id;
END;

INSERT INTO users (email, display_name, password_hash)
VALUES ('admin@example.com','Admin','$2b$12$5y/F/S7SI.7bVIh4mjccXuAt6KIODgFRtZGqHM9OYhn7n2MrUI.ha');

-- =========================
-- jobs
-- =========================

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,

  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,

  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','running','completed','error')),

  total_children INTEGER DEFAULT 0 CHECK(total_children >=0),
  completed_children INTEGER DEFAULT 0 CHECK(completed_children >=0),

  error_message TEXT,

  type TEXT NOT NULL
    CHECK(type IN ('transcribe','translate')),

  output_type TEXT NOT NULL
    CHECK(output_type IN ('text','srt')),

  input_language TEXT NOT NULL,
  output_language TEXT,

  transcribe_file_path TEXT,
  translate_file_path TEXT,

  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

  metadata TEXT DEFAULT '{}'
);

CREATE INDEX idx_jobs_user_id ON jobs(user_id);
CREATE INDEX idx_jobs_status ON jobs(status);

-- =========================
-- job_children
-- =========================

CREATE TABLE job_children (
  id INTEGER PRIMARY KEY AUTOINCREMENT,

  job_id INTEGER NOT NULL
    REFERENCES jobs(id) ON DELETE CASCADE,

  input_file_path TEXT NOT NULL,

  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','running','completed','error')),

  error_message TEXT,

  metadata TEXT DEFAULT '{}',

  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE whisper_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_child_id INTEGER NOT NULL REFERENCES job_children(id) ON DELETE CASCADE,
  input_file_path TEXT NOT NULL,
  offset INTEGER NOT NULL DEFAULT 0,
  input_language TEXT NOT NULL,
  output_language TEXT,
  type TEXT NOT NULL CHECK(type IN ('transcribe','translate')),
  output_type TEXT NOT NULL CHECK(output_type IN ('text','srt')),
  transcribe_file_path TEXT,
  translate_file_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','running','completed','error')),

  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE nllb_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_child_id INTEGER NOT NULL REFERENCES job_children(id) ON DELETE CASCADE,
  input_file_path TEXT NOT NULL,
  offset INTEGER NOT NULL DEFAULT 0,
  input_language TEXT NOT NULL,
  output_language TEXT,
  type TEXT NOT NULL CHECK(type IN ('transcribe','translate')),
  output_type TEXT NOT NULL CHECK(output_type IN ('text','srt')),
  transcribe_file_path TEXT,
  translate_file_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','running','completed','error')),
    
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_job_children_job_id ON job_children(job_id);
CREATE INDEX idx_job_children_status ON job_children(status);

-- =========================
-- sync parent job trigger
-- =========================

CREATE TRIGGER trg_child_insert
AFTER INSERT ON job_children
BEGIN
  UPDATE jobs
  SET total_children =
    (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id),

  completed_children =
    (SELECT COUNT(*) FROM job_children
      WHERE job_id = NEW.job_id
      AND status='completed')

  WHERE id = NEW.job_id;
END;

CREATE TRIGGER trg_child_update
AFTER UPDATE ON job_children
BEGIN
  UPDATE jobs
  SET total_children =
    (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id),

  completed_children =
    (SELECT COUNT(*) FROM job_children
      WHERE job_id = NEW.job_id
      AND status='completed')

  WHERE id = NEW.job_id;
END;

CREATE TRIGGER trg_child_delete
AFTER DELETE ON job_children
BEGIN
  UPDATE jobs
  SET total_children =
    (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id),

  completed_children =
    (SELECT COUNT(*) FROM job_children
      WHERE job_id = OLD.job_id
      AND status='completed')

  WHERE id = OLD.job_id;
END;