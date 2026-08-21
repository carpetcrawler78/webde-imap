import json
import tempfile
import unittest
from pathlib import Path

from webde_imap.state import (
    get_folder_state,
    load_state,
    reconcile_folder_uidvalidity,
    save_state,
    set_folder_state,
)


class LoadSaveStateTestCase(unittest.TestCase):
    def test_load_missing_file_returns_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = load_state(Path(tmp) / "webde_state.json")
            self.assertEqual(state["folders"], {})
            self.assertIsNone(state["last_run_status"])

    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "webde_state.json"
            state = {
                "version": 2,
                "folders": {
                    "INBOX": {"uidvalidity": 1000, "last_processed_uid": 42},
                    "Social Media": {"uidvalidity": 2000, "last_processed_uid": 7},
                },
                "last_run_at_utc": "2026-08-21T10:00:00+00:00",
                "last_run_status": "OK",
            }
            save_state(state, path)
            loaded = load_state(path)
            self.assertEqual(loaded, state)

    def test_migrates_v1_single_folder_schema_without_losing_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "webde_state.json"
            v1_state = {
                "version": 1,
                "folder": "INBOX",
                "uidvalidity": 1,
                "last_processed_uid": 1374040402,
                "last_run_at_utc": "2026-08-21T13:46:29+00:00",
                "last_run_status": "OK",
            }
            path.write_text(json.dumps(v1_state), encoding="utf-8")

            loaded = load_state(path)

            self.assertEqual(
                loaded["folders"]["INBOX"],
                {"uidvalidity": 1, "last_processed_uid": 1374040402},
            )
            self.assertEqual(loaded["last_run_status"], "OK")

    def test_saved_state_contains_no_mail_content_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "webde_state.json"
            state = {
                "version": 2,
                "folders": {"INBOX": {"uidvalidity": 1000, "last_processed_uid": 42}},
                "last_run_at_utc": "2026-08-21T10:00:00+00:00",
                "last_run_status": "OK",
            }
            save_state(state, path)
            parsed = json.loads(path.read_text(encoding="utf-8"))
            forbidden_keys = {"subject", "from", "to", "body", "excerpt", "url", "attachment"}
            self.assertEqual(set(parsed.keys()) & forbidden_keys, set())


class FolderStateHelpersTestCase(unittest.TestCase):
    def test_get_folder_state_defaults_when_missing(self):
        state = {"folders": {}}
        self.assertEqual(get_folder_state(state, "INBOX"), {"uidvalidity": None, "last_processed_uid": None})

    def test_set_and_get_folder_state_round_trip(self):
        state = {"folders": {}}
        set_folder_state(state, "Social Media", uidvalidity=1234, last_processed_uid=99)
        self.assertEqual(
            get_folder_state(state, "Social Media"),
            {"uidvalidity": 1234, "last_processed_uid": 99},
        )
        # other folders remain untouched
        self.assertEqual(get_folder_state(state, "INBOX"), {"uidvalidity": None, "last_processed_uid": None})


class ReconcileFolderUidvalidityTestCase(unittest.TestCase):
    def test_bootstraps_when_no_prior_state(self):
        folder_state = {"uidvalidity": None, "last_processed_uid": None}
        new_state, was_reset = reconcile_folder_uidvalidity(folder_state, 1000, highest_uid_fn=lambda: 500)
        self.assertTrue(was_reset)
        self.assertEqual(new_state, {"uidvalidity": 1000, "last_processed_uid": 500})

    def test_no_reset_when_uidvalidity_matches(self):
        folder_state = {"uidvalidity": 1000, "last_processed_uid": 42}
        called = []
        new_state, was_reset = reconcile_folder_uidvalidity(
            folder_state, 1000, highest_uid_fn=lambda: called.append(1) or 999
        )
        self.assertFalse(was_reset)
        self.assertEqual(new_state["last_processed_uid"], 42)
        self.assertEqual(called, [])  # highest_uid_fn must not be called on the fast path

    def test_uidvalidity_change_triggers_rebootstrap_without_mass_resend(self):
        folder_state = {"uidvalidity": 1000, "last_processed_uid": 42}
        new_state, was_reset = reconcile_folder_uidvalidity(folder_state, 2000, highest_uid_fn=lambda: 900)
        self.assertTrue(was_reset)
        self.assertEqual(new_state, {"uidvalidity": 2000, "last_processed_uid": 900})


if __name__ == "__main__":
    unittest.main()
