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
{track}scores: data_use=0.81 ai=0.10
---

# {vendor} {doc} changed — {date}

**Removed:**
> We may share your data with partners.

**Added:**
> We may share your data with partners to train models.

Source: [https://example.com/policy](https://example.com/policy)
"""


def write_alert(alerts: pathlib.Path, vendor: str, doc: str, date: str, track: str = "") -> str:
    stem = f"{date}-{vendor.lower()}-{doc.lower().replace(' ', '-')}"
    (alerts / f"{stem}.md").write_text(ALERT.format(
        vendor=vendor, doc=doc, date=date, track=f"track: {track}\n" if track else ""))
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

    # Buttondown's tag name -> id table, as GET /v1/tags would return it.
    TAGS = {"service:gmail": "t-gmail", "track:typing": "t-typing",
            "service:spotify": "t-spotify", "track:consumer": "t-consumer", "site": "t-site"}

    def run_once(self):
        return draft_emails.run(self.alerts, self.state, api_key="test-key",
                                post=self.fake_post, tags=lambda key: self.TAGS)

    def test_alert_is_addressed_to_its_service_track_and_untagged_subscribers(self):
        self.run_once()
        write_alert(self.alerts, "Gmail", "Privacy Policy", "2026-09-30", track="typing")
        self.run_once()
        f = self.calls[0]["payload"]["filters"]
        self.assertEqual(f["predicate"], "or")
        self.assertEqual({x["value"] for x in f["filters"]}, {"t-gmail", "t-typing"})
        self.assertTrue(all(x["operator"] == "contains" for x in f["filters"]))
        (untagged,) = f["groups"]  # no service:/track: tag at all; Spotify-only readers are out
        self.assertEqual(untagged["predicate"], "and")
        self.assertEqual({x["value"] for x in untagged["filters"]},
                         {"t-gmail", "t-typing", "t-spotify", "t-consumer"})
        self.assertTrue(all(x["operator"] == "not_contains" for x in untagged["filters"]))

    def test_alert_with_no_known_service_still_drafts_to_everyone(self):
        self.run_once()
        write_alert(self.alerts, "Zorpmail", "Privacy Policy", "2026-09-30")  # not on the watchlist, no track
        self.run_once()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["payload"]["status"], "draft")
        self.assertNotIn("filters", self.calls[0]["payload"])

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
