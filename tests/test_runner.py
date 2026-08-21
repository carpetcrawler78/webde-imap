import logging
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from webde_imap import runner
from webde_imap.state import load_state

from fake_imap import FakeIMAPConnection
from helpers import make_plain_message

CONFIG = {
    "imap_username": "user@web.de",
    "folders": ["INBOX"],
    "smtp_server": "smtp.web.de",
    "smtp_port": 587,
    "smtp_username": "user@web.de",
    "smtp_password": "secret",
    "forward_to": "user@gmail.com",
    "max_attachment_total_bytes": 10 * 1024 * 1024,
    "max_body_chars": None,
}

EMPTY_RULES = {
    "job_platforms": [],
    "message_platforms": [],
    "application_keywords": {"subject_or_body": []},
    "important_keywords": {"subject_or_body": []},
    "ignore_signals": {"subject_or_body_keywords": []},
}


def _raw(subject, from_addr="sender@example.com", body="Text"):
    return make_plain_message(subject=subject, from_addr=from_addr, body=body).as_bytes()


class RunnerBootstrapTestCase(unittest.TestCase):
    def test_bootstrap_does_not_import_historical_mail_by_default(self):
        mail = FakeIMAPConnection(uidvalidity=1000)
        mail.add_message("INBOX", 1, _raw("Alt 1"))
        mail.add_message("INBOX", 2, _raw("Alt 2"))

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch("webde_imap.forward.send_via_smtp") as send_mock:
                result = runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)

            self.assertEqual(result.folder("INBOX").processed_uids, [])
            send_mock.assert_not_called()
            state = load_state(state_path)
            self.assertEqual(state["folders"]["INBOX"]["last_processed_uid"], 2)


class RunnerRetryTestCase(unittest.TestCase):
    def test_repeated_run_with_same_state_does_not_resend(self):
        mail = FakeIMAPConnection(uidvalidity=1000)
        mail.add_message("INBOX", 1, _raw("Alt 1"))

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch("webde_imap.forward.send_via_smtp"):
                runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)  # bootstrap

            mail.add_message("INBOX", 2, _raw("Neu", from_addr="hr@example.com"))
            with patch("webde_imap.forward.send_via_smtp") as send_mock:
                result = runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)
            self.assertEqual(result.folder("INBOX").processed_uids, [2])
            self.assertEqual(send_mock.call_count, 1)

            with patch("webde_imap.forward.send_via_smtp") as send_mock_2:
                result2 = runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)
            self.assertEqual(result2.folder("INBOX").processed_uids, [])
            send_mock_2.assert_not_called()


class RunnerFailureTestCase(unittest.TestCase):
    def test_smtp_failure_does_not_advance_past_uid(self):
        mail = FakeIMAPConnection(uidvalidity=1000)
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch("webde_imap.forward.send_via_smtp"):
                runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)  # bootstrap

            mail.add_message("INBOX", 1, _raw("Erste"))
            mail.add_message("INBOX", 2, _raw("Zweite"))

            with patch("webde_imap.forward.send_via_smtp", side_effect=RuntimeError("SMTP down")):
                result = runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)

            folder_result = result.folder("INBOX")
            self.assertTrue(folder_result.had_failure)
            self.assertEqual(folder_result.failed_uid, 1)
            self.assertEqual(folder_result.processed_uids, [])
            state = load_state(state_path)
            self.assertEqual(state["last_run_status"], "PARTIAL")
            self.assertEqual(state["folders"]["INBOX"]["last_processed_uid"], 0)

    def test_partial_success_persists_progress_up_to_last_success(self):
        mail = FakeIMAPConnection(uidvalidity=1000)
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch("webde_imap.forward.send_via_smtp"):
                runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)  # bootstrap

            mail.add_message("INBOX", 1, _raw("Erste"))
            mail.add_message("INBOX", 2, _raw("Zweite"))

            call_count = {"n": 0}

            def side_effect(_msg, _config):
                call_count["n"] += 1
                if call_count["n"] == 2:
                    raise RuntimeError("SMTP down")

            with patch("webde_imap.forward.send_via_smtp", side_effect=side_effect):
                result = runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)

            self.assertTrue(result.had_failure)
            self.assertEqual(result.folder("INBOX").processed_uids, [1])
            state = load_state(state_path)
            self.assertEqual(state["folders"]["INBOX"]["last_processed_uid"], 1)
            self.assertEqual(state["last_run_status"], "PARTIAL")

            mail.add_message("INBOX", 3, _raw("Dritte"))
            with patch("webde_imap.forward.send_via_smtp") as send_mock:
                result2 = runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)
            self.assertEqual(result2.folder("INBOX").processed_uids, [2, 3])
            self.assertEqual(send_mock.call_count, 2)


class RunnerUidvalidityChangeTestCase(unittest.TestCase):
    def test_uidvalidity_change_triggers_rebootstrap_without_mass_resend(self):
        mail = FakeIMAPConnection(uidvalidity=1000)
        mail.add_message("INBOX", 1, _raw("Alt"))
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch("webde_imap.forward.send_via_smtp"):
                runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)  # bootstrap

            mail.set_uidvalidity("INBOX", 2000)  # simulate a server-side UIDVALIDITY reset
            mail.messages["INBOX"] = {}
            mail.add_message("INBOX", 1, _raw("Neu nach Reset"))
            mail.add_message("INBOX", 2, _raw("Neu nach Reset 2"))

            with patch("webde_imap.forward.send_via_smtp") as send_mock:
                result = runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)

            self.assertEqual(result.folder("INBOX").processed_uids, [])
            send_mock.assert_not_called()
            state = load_state(state_path)
            self.assertEqual(state["folders"]["INBOX"]["uidvalidity"], 2000)
            self.assertEqual(state["folders"]["INBOX"]["last_processed_uid"], 2)


class RunnerDryRunTestCase(unittest.TestCase):
    def test_dry_run_does_not_send_or_persist_state(self):
        mail = FakeIMAPConnection(uidvalidity=1000)
        mail.add_message("INBOX", 1, _raw("Alt"))
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch("webde_imap.forward.send_via_smtp"):
                runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)  # bootstrap

            mail.add_message("INBOX", 2, _raw("Neu"))
            with patch("webde_imap.forward.send_via_smtp") as send_mock:
                result = runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=True)

            send_mock.assert_not_called()
            self.assertEqual(result.folder("INBOX").processed_uids, [2])
            self.assertEqual(result.forwarded_count, 1)
            state = load_state(state_path)
            self.assertEqual(state["folders"]["INBOX"]["last_processed_uid"], 1)  # untouched by the dry run


class RunnerMultiFolderTestCase(unittest.TestCase):
    def test_folders_are_processed_independently(self):
        mail = FakeIMAPConnection(uidvalidity=1000)
        mail.add_message("INBOX", 1, _raw("Inbox Alt"))
        mail.add_message("Social Media", 1, _raw("Social Alt"))

        config = dict(CONFIG, folders=["INBOX", "Social Media"])

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch("webde_imap.forward.send_via_smtp"):
                runner.run(mail, config, EMPTY_RULES, state_path, dry_run=False)  # bootstrap both

            mail.add_message("INBOX", 2, _raw("Inbox Neu"))
            mail.add_message("Social Media", 2, _raw("Social Neu"))

            with patch("webde_imap.forward.send_via_smtp") as send_mock:
                result = runner.run(mail, config, EMPTY_RULES, state_path, dry_run=False)

            self.assertEqual(result.folder("INBOX").processed_uids, [2])
            self.assertEqual(result.folder("Social Media").processed_uids, [2])
            self.assertEqual(send_mock.call_count, 2)
            state = load_state(state_path)
            self.assertEqual(state["folders"]["INBOX"]["last_processed_uid"], 2)
            self.assertEqual(state["folders"]["Social Media"]["last_processed_uid"], 2)

    def test_failure_in_one_folder_does_not_block_the_other(self):
        mail = FakeIMAPConnection(uidvalidity=1000)
        config = dict(CONFIG, folders=["INBOX", "Social Media"])

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch("webde_imap.forward.send_via_smtp"):
                runner.run(mail, config, EMPTY_RULES, state_path, dry_run=False)  # bootstrap both

            mail.add_message("INBOX", 1, _raw("Inbox Neu"))
            mail.add_message("Social Media", 1, _raw("Social Neu"))

            def side_effect(msg, _config):
                if "[WEBDE][REVIEW] Inbox Neu" in msg["Subject"]:
                    raise RuntimeError("SMTP down")

            with patch("webde_imap.forward.send_via_smtp", side_effect=side_effect):
                result = runner.run(mail, config, EMPTY_RULES, state_path, dry_run=False)

            self.assertTrue(result.folder("INBOX").had_failure)
            self.assertFalse(result.folder("Social Media").had_failure)
            self.assertEqual(result.folder("Social Media").processed_uids, [1])
            self.assertTrue(result.had_failure)  # overall run still reported as failed


class RunnerLoggingTestCase(unittest.TestCase):
    def test_no_mail_content_in_logs(self):
        mail = FakeIMAPConnection(uidvalidity=1000)
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch("webde_imap.forward.send_via_smtp"):
                runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)  # bootstrap

            secret_subject = "GEHEIMER BETREFF: streng vertraulich XYZ123"
            secret_body = "Streng vertraulicher Mailinhalt ABC789"
            mail.add_message("INBOX", 1, _raw(secret_subject, body=secret_body))

            log_stream = StringIO()
            handler = logging.StreamHandler(log_stream)
            previous_level = runner.logger.level
            runner.logger.addHandler(handler)
            runner.logger.setLevel(logging.INFO)
            try:
                with patch("webde_imap.forward.send_via_smtp"):
                    runner.run(mail, CONFIG, EMPTY_RULES, state_path, dry_run=False)
            finally:
                runner.logger.removeHandler(handler)
                runner.logger.setLevel(previous_level)

            log_output = log_stream.getvalue()
            self.assertNotIn(secret_subject, log_output)
            self.assertNotIn(secret_body, log_output)


if __name__ == "__main__":
    unittest.main()
