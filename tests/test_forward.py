import unittest
from unittest.mock import MagicMock, patch

from webde_imap.forward import build_forward_message, select_attachments_within_cap, send_via_smtp
from webde_imap.mail_content import AttachmentInfo, extract_message_content

from helpers import add_attachment, make_plain_message, reload_message

BASE_CONFIG = {
    "smtp_server": "smtp.web.de",
    "smtp_port": 587,
    "smtp_username": "me@web.de",
    "smtp_password": "secret",
    "forward_to": "me@gmail.com",
    "max_attachment_total_bytes": 10 * 1024 * 1024,
    "max_body_chars": None,
}


class SelectAttachmentsWithinCapTestCase(unittest.TestCase):
    def test_attachment_below_cap_is_forwarded(self):
        att = AttachmentInfo("a.pdf", "application/pdf", 1000, b"x" * 1000)
        forwarded, skipped = select_attachments_within_cap([att], cap_bytes=10_000)
        self.assertEqual(forwarded, [att])
        self.assertEqual(skipped, [])

    def test_attachment_above_cap_is_listed_metadata_only(self):
        att = AttachmentInfo("big.zip", "application/zip", 20_000, b"x" * 20_000)
        forwarded, skipped = select_attachments_within_cap([att], cap_bytes=10_000)
        self.assertEqual(forwarded, [])
        self.assertEqual(len(skipped), 1)
        self.assertIs(skipped[0][0], att)
        self.assertIn("nicht weitergeleitet", skipped[0][1])

    def test_running_total_across_multiple_attachments_respects_cap(self):
        small = AttachmentInfo("small.pdf", "application/pdf", 6_000, b"x" * 6_000)
        also_small = AttachmentInfo("also.pdf", "application/pdf", 6_000, b"x" * 6_000)
        forwarded, skipped = select_attachments_within_cap([small, also_small], cap_bytes=10_000)
        self.assertEqual(forwarded, [small])
        self.assertEqual([s[0] for s in skipped], [also_small])

    def test_undecodable_attachment_is_never_forwarded(self):
        att = AttachmentInfo("broken.bin", "application/octet-stream", 0, None)
        forwarded, skipped = select_attachments_within_cap([att], cap_bytes=10_000)
        self.assertEqual(forwarded, [])
        self.assertIn("nicht weitergeleitet", skipped[0][1])


class BuildForwardMessageTestCase(unittest.TestCase):
    def test_subject_prefixed_and_header_block_present(self):
        msg = reload_message(
            make_plain_message(subject="Wichtige Info", from_addr="sender@example.com", body="Inhalt der Mail.")
        )
        content = extract_message_content(msg)
        forward_msg = build_forward_message(msg, content, "IMPORTANT", "webde-abc123", "INBOX", 42, BASE_CONFIG)

        self.assertEqual(forward_msg["Subject"], "[WEBDE][IMPORTANT] Wichtige Info")
        body = forward_msg.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("WEBDE_EVENT_ID: webde-abc123", body)
        self.assertIn("WEBDE_CATEGORY: IMPORTANT", body)
        self.assertIn("ORIGINAL_UID: 42", body)
        self.assertIn("Inhalt der Mail.", body)

    def test_no_original_mail_content_leaks_into_attachments_over_cap(self):
        msg = make_plain_message(subject="Mit grossem Anhang", from_addr="a@example.com", body="Text")
        add_attachment(msg, "huge.bin", b"x" * 1000, mime_type="application/octet-stream")
        msg = reload_message(msg)
        content = extract_message_content(msg)

        small_cap_config = dict(BASE_CONFIG, max_attachment_total_bytes=10)
        forward_msg = build_forward_message(msg, content, "REVIEW", "webde-xyz", "INBOX", 1, small_cap_config)

        self.assertEqual(len(list(forward_msg.iter_attachments())), 0)
        body = forward_msg.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("huge.bin", body)
        self.assertIn("nicht weitergeleitet", body)


class SendViaSmtpTestCase(unittest.TestCase):
    def test_sends_via_starttls_login_and_send_message(self):
        msg = reload_message(make_plain_message(subject="X", from_addr="a@example.com", body="Y"))
        content = extract_message_content(msg)
        forward_msg = build_forward_message(msg, content, "REVIEW", "webde-1", "INBOX", 1, BASE_CONFIG)

        with patch("webde_imap.forward.smtplib.SMTP") as smtp_cls:
            smtp_instance = MagicMock()
            smtp_cls.return_value.__enter__.return_value = smtp_instance
            send_via_smtp(forward_msg, BASE_CONFIG)

            smtp_instance.starttls.assert_called_once()
            smtp_instance.login.assert_called_once_with(BASE_CONFIG["smtp_username"], BASE_CONFIG["smtp_password"])
            smtp_instance.send_message.assert_called_once_with(forward_msg)


if __name__ == "__main__":
    unittest.main()
