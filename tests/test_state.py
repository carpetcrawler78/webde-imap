import json
import tempfile
import unittest
from pathlib import Path

from webde_imap.state import load_state, reconcile_uidvalidity, save_state


class LoadSaveStateTestCase(unittest.TestCase):
    def test_load_missing_file_returns_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = load_state(Path(tmp) / "webde_state.json", folder="INBOX")
            self.assertIsNone(state["uidvalidity"])
            self.assertIsNone(state["last_processed_uid"])
            self.assertEqual(state["folder"], "INBOX")

    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "webde_state.json"
            state = {
                "version": 1,
                "folder": "INBOX",
                "uidvalidity": 1000,
                "last_processed_uid": 42,
                "last_run_at_utc": "2026-08-21T10:00:00+00:00",
                "last_run_status": "OK",
            }
            save_state(state, path)
            loaded = load_state(path)
            self.assertEqual(loaded, state)

    def test_saved_state_contains_no_mail_content_fields(self):
        # Guard against accidental future additions of content-bearing fields.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "webde_state.json"
            state = {
                "version": 1,
                "folder": "INBOX",
                "uidvalidity": 1000,
                "last_processed_uid": 42,
                "last_run_at_utc": "2026-08-21T10:00:00+00:00",
                "last_run_status": "OK",
            }
            save_state(state, path)
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            forbidden_keys = {"subject", "from", "to", "body", "excerpt", "url", "attachment"}
            self.assertEqual(set(parsed.keys()) & forbidden_keys, set())


class ReconcileUidvalidityTestCase(unittest.TestCase):
    def test_bootstraps_when_no_prior_state(self):
        state = {"uidvalidity": None, "last_processed_uid": None}
        new_state, was_reset = reconcile_uidvalidity(state, "INBOX", 1000, highest_uid_fn=lambda: 500)
        self.assertTrue(was_reset)
        self.assertEqual(new_state["uidvalidity"], 1000)
        self.assertEqual(new_state["last_processed_uid"], 500)

    def test_no_reset_when_uidvalidity_matches(self):
        state = {"uidvalidity": 1000, "last_processed_uid": 42}
        called = []
        new_state, was_reset = reconcile_uidvalidity(
            state, "INBOX", 1000, highest_uid_fn=lambda: called.append(1) or 999
        )
        self.assertFalse(was_reset)
        self.assertEqual(new_state["last_processed_uid"], 42)
        self.assertEqual(called, [])  # highest_uid_fn must not be called on the fast path

    def test_uidvalidity_change_triggers_rebootstrap_without_mass_resend(self):
        state = {"uidvalidity": 1000, "last_processed_uid": 42}
        new_state, was_reset = reconcile_uidvalidity(state, "INBOX", 2000, highest_uid_fn=lambda: 900)
        self.assertTrue(was_reset)
        self.assertEqual(new_state["uidvalidity"], 2000)
        # jumps straight to the current highest UID -- nothing between old and new gets resent
        self.assertEqual(new_state["last_processed_uid"], 900)


if __name__ == "__main__":
    unittest.main()
