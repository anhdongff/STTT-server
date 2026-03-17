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
  SELECT NEW.updated_at = CURRENT_TIMESTAMP;
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

CREATE TABLE whisper_large_queue (
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
-- sync parent job triggers (mirror logic from Postgres schema)
-- - update total_children and completed_children
-- - if any child has status='error' -> set parent to 'error' and propagate error_message
-- - if all children completed -> set parent to 'completed' (when parent is pending or running)
-- - if any child running -> set parent to 'running' when parent is 'pending'
-- We implement three triggers: AFTER INSERT, AFTER UPDATE, AFTER DELETE.

CREATE TRIGGER trg_child_after_insert
AFTER INSERT ON job_children
BEGIN
  -- refresh counters
  UPDATE jobs
  SET
    total_children = (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id),
    completed_children = (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id AND status = 'completed')
  WHERE id = NEW.job_id;

  -- if any child errored -> mark parent error and propagate latest error_message when available
  UPDATE jobs
  SET
    status = 'error',
    error_message = COALESCE((SELECT error_message FROM job_children WHERE job_id = NEW.job_id AND error_message IS NOT NULL ORDER BY updated_at DESC LIMIT 1), error_message)
  WHERE id = NEW.job_id
    AND EXISTS (SELECT 1 FROM job_children WHERE job_id = NEW.job_id AND status = 'error');

  -- all children completed -> mark parent completed
  UPDATE jobs
  SET status = 'completed'
  WHERE id = NEW.job_id
    AND (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id) > 0
    AND (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id AND status = 'completed') = (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id)
    AND status IN ('pending','running');

  -- any child running -> mark parent running when parent is pending
  UPDATE jobs
  SET status = 'running'
  WHERE id = NEW.job_id
    AND status = 'pending'
    AND EXISTS (SELECT 1 FROM job_children WHERE job_id = NEW.job_id AND status = 'running');
END;

CREATE TRIGGER trg_child_after_update
AFTER UPDATE ON job_children
BEGIN
  -- refresh counters for new parent
  UPDATE jobs
  SET
    total_children = (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id),
    completed_children = (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id AND status = 'completed'),
    updated_at = CURRENT_TIMESTAMP
  WHERE id = NEW.job_id;

  -- handle errors for new parent
  UPDATE jobs
  SET
    status = 'error',
    error_message = COALESCE((SELECT error_message FROM job_children WHERE job_id = NEW.job_id AND error_message IS NOT NULL ORDER BY updated_at DESC LIMIT 1), error_message),
    updated_at = CURRENT_TIMESTAMP
  WHERE id = NEW.job_id
    AND EXISTS (SELECT 1 FROM job_children WHERE job_id = NEW.job_id AND status = 'error');

  -- all children completed -> mark parent completed
  UPDATE jobs
  SET status = 'completed',
      updated_at = CURRENT_TIMESTAMP
  WHERE id = NEW.job_id
    AND (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id) > 0
    AND (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id AND status = 'completed') = (SELECT COUNT(*) FROM job_children WHERE job_id = NEW.job_id)
    AND status IN ('pending','running');

  -- any child running -> mark parent running when parent is pending
  UPDATE jobs
  SET status = 'running'
  WHERE id = NEW.job_id
    AND status = 'pending'
    AND EXISTS (SELECT 1 FROM job_children WHERE job_id = NEW.job_id AND status = 'running');

  -- if job_id changed, refresh the old parent as well
  UPDATE jobs
  SET
    total_children = (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id),
    completed_children = (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id AND status = 'completed'),
    updated_at = CURRENT_TIMESTAMP
  WHERE id = OLD.job_id
    AND (OLD.job_id IS NOT NULL AND OLD.job_id != NEW.job_id);

  UPDATE jobs
  SET
    status = 'error',
    error_message = COALESCE((SELECT error_message FROM job_children WHERE job_id = OLD.job_id AND error_message IS NOT NULL ORDER BY updated_at DESC LIMIT 1), error_message),
    updated_at = CURRENT_TIMESTAMP
  WHERE id = OLD.job_id
    AND (OLD.job_id IS NOT NULL AND OLD.job_id != NEW.job_id)
    AND EXISTS (SELECT 1 FROM job_children WHERE job_id = OLD.job_id AND status = 'error');

  UPDATE jobs
  SET status = 'completed',
      updated_at = CURRENT_TIMESTAMP
  WHERE id = OLD.job_id
    AND (OLD.job_id IS NOT NULL AND OLD.job_id != NEW.job_id)
    AND (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id) > 0
    AND (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id AND status = 'completed') = (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id)
    AND status IN ('pending','running');

  UPDATE jobs
  SET status = 'running'
  WHERE id = OLD.job_id
    AND (OLD.job_id IS NOT NULL AND OLD.job_id != NEW.job_id)
    AND status = 'pending'
    AND EXISTS (SELECT 1 FROM job_children WHERE job_id = OLD.job_id AND status = 'running');
END;

CREATE TRIGGER trg_child_after_delete
AFTER DELETE ON job_children
BEGIN
  -- refresh counters
  UPDATE jobs
  SET
    total_children = (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id),
    completed_children = (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id AND status = 'completed')
  WHERE id = OLD.job_id;

  -- if any child errored -> mark parent error and propagate latest error_message when available
  UPDATE jobs
  SET
    status = 'error',
    error_message = COALESCE((SELECT error_message FROM job_children WHERE job_id = OLD.job_id AND error_message IS NOT NULL ORDER BY updated_at DESC LIMIT 1), error_message)
  WHERE id = OLD.job_id
    AND EXISTS (SELECT 1 FROM job_children WHERE job_id = OLD.job_id AND status = 'error');

  -- all children completed -> mark parent completed
  UPDATE jobs
  SET status = 'completed'
  WHERE id = OLD.job_id
    AND (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id) > 0
    AND (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id AND status = 'completed') = (SELECT COUNT(*) FROM job_children WHERE job_id = OLD.job_id)
    AND status IN ('pending','running');

  -- any child running -> mark parent running when parent is pending
  UPDATE jobs
  SET status = 'running'
  WHERE id = OLD.job_id
    AND status = 'pending'
    AND EXISTS (SELECT 1 FROM job_children WHERE job_id = OLD.job_id AND status = 'running');
END;