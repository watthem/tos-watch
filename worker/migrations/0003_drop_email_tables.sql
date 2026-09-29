-- 2026-09-28 (owner: "remove the PII as early as possible from the services"):
-- Buttondown is the only place an email address lives. Sources, topics, votes
-- and service requests are Buttondown tags on the subscriber. D1 keeps only the
-- anonymous per-service request counts in `requests`.
DROP TABLE IF EXISTS votes;
DROP TABLE IF EXISTS subscribers;
