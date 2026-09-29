-- 2026-09-28: anonymous per-feature interest counts for POST /vote (owner:
-- "we should collect signals"). Buttondown can only record a vote:<feature>
-- tag on a plan with tags, so without this count a vote is lost. No email,
-- no IP: a feature id, a count, a timestamp. Unverified, like requests.
CREATE TABLE feature_counts (
  feature TEXT PRIMARY KEY,
  unverified_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
