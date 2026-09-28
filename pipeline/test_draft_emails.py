#!/usr/bin/env python3
"""End-to-end checks for pipeline/draft_emails.py with the HTTP call mocked.

Kept because the failure modes are real harm, not style: an alert drafted
twice, a back-catalog of history drafted on first run, or a payload that
isn't a draft (Buttondown would email every subscriber unreviewed).

    python3 pipeline/test_draft_emails.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import draft_emails  # noqa: E402

ALERT = """---
vendor: {vendor}
doc: {doc}
date: {date}
source_url: https://example.com/policy
scores: data_use=0.81 ai=0.10
---

# {vendor} {doc} changed — {date}

**Removed:**
> We may share your data with partners.

**Added:**
> We may share your data with partners to train models.

Source: [https://example.com/policy](https://example.com/policy)
"""


def write_alert(alerts: pathlib.Path, vendor: str, doc: str, date: str) -> str:
    stem = f"{date}-{vendor.lower()}-{doc.lower().replace(' ', '-')}"
    (alerts / f"{stem}.md").write_text(ALERT.format(vendor=vendor, doc=doc, date=date))
    return stem


class DraftEmailsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.alerts = root / "alerts"
        self.alerts.mkdir()
        self.state = root / "drafts_state.json"
        self.calls: list[dict] = []
        # History that already exists before the drafter ever runs.
        write_alert(self.alerts, "Facebook", "Privacy Policy", "2024-06-26")
        write_alert(self.alerts, "Gmail", "Terms of Service", "2026-09-23")

    def tearDown(self):
        self.tmp.cleanup()

    def fake_post(self, payload, idempotency_key, api_key):
        self.calls.append({"payload": payload, "key": idempotency_key, "api_key": api_key})
        return {"id": f"em_{len(self.calls)}", "status": payload["status"]}

    def run_once(self):
        return draft_emails.run(self.alerts, self.state, api_key="test-key", post=self.fake_post)

    def test_new_alert_becomes_one_draft_and_rerun_drafts_nothing(self):
        self.run_once()  # first run: seeds the high-water mark from history
        self.assertEqual(self.calls, [])

        stem = write_alert(self.alerts, "ChatGPT", "Privacy Policy", "2026-09-30")
        self.run_once()
        self.assertEqual(len(self.calls), 1)
        payload = self.calls[0]["payload"]
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["subject"], "ChatGPT changed its privacy policy")
        self.assertIn(f"https://tos.watch/alerts/{stem}.html", payload["body"])
        self.assertIn("to train models", payload["body"])
        self.assertIn("ODC-By", payload["body"])

        self.run_once()
        self.assertEqual(len(self.calls), 1, "re-run must not draft the same alert again")

    def test_first_run_drafts_no_history(self):
        self.run_once()
        self.assertEqual(self.calls, [])
        # A backfilled old version (e.g. a new vendor's history) stays undrafted too.
        write_alert(self.alerts, "Reddit", "Privacy Policy", "2025-06-02")
        self.run_once()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
