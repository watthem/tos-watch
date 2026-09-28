CREATE TABLE subscribers (
  email TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'pending', -- pending | confirmed | unsubscribed
  token TEXT NOT NULL,
  source TEXT,
  created_at TEXT NOT NULL,
  confirmed_at TEXT
);

CREATE INDEX idx_subscribers_token ON subscribers(token);
