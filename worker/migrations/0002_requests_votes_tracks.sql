-- 2026-09-26: tracks + search/subscribe/request/self-host expansion.

-- A reader picks which tracks they want to hear about (checkboxes on the
-- landing page, default all). Comma-separated known track ids; validated
-- and never stored raw by the Worker (see src/index.js VALID_TRACKS).
ALTER TABLE subscribers ADD COLUMN tracks TEXT;

-- POST /request: "search for a site, request it for the newsletter."
-- unverified_count is a raw, anonymous-safe interest counter, bumped on
-- every request for the same normalized service/url. Verified interest
-- (from a confirmed email) is tracked in votes (feature = 'requested:<slug>'),
-- not duplicated here.
CREATE TABLE requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  normalized_key TEXT NOT NULL UNIQUE,
  service TEXT,
  url TEXT,
  unverified_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- POST /vote: a verified +1 on a feature request (e.g. "on-demand-check"),
-- or a verified per-service request tagged 'requested:<slug>' from
-- POST /request when an email is given. A vote only counts once its
-- subscriber confirms (see the /confirm route); at most one vote per
-- (feature, email).
CREATE TABLE votes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feature TEXT NOT NULL,
  email TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', -- pending | confirmed
  created_at TEXT NOT NULL,
  confirmed_at TEXT,
  UNIQUE(feature, email)
);

CREATE INDEX idx_votes_feature_status ON votes(feature, status);
