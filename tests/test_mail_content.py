import unittest
from email.header import Header

from webde_imap.mail_content import decode_mime_header, extract_attachments, extract_full_body

from helpers import add_attachment, make_html_only_message, make_plain_message, reload_message


class DecodeMimeHeaderTestCase(unittest.TestCase):
    def test_strips_embedded_newlines_from_encoded_header(self):
        # Real-world case: some senders (e.g. GitHub Actions notifications) produce
        # RFC 2047 encoded-word subjects that decode to a literal embedded newline.
        # Reusing this as an outgoing header value must not raise or break layout.
        header = Header("Run failed: Ntfy Outbox - main\n (89ee304)", "utf-8")
        decoded = decode_mime_header(header.encode())
        self.assertNotIn("\n", decoded)
        self.assertNotIn("\r", decoded)
        self.assertIn("Run failed: Ntfy Outbox - main", decoded)
        self.assertIn("(89ee304)", decoded)

    def test_collapses_double_spaces_left_by_newline_removal(self):
        decoded = decode_mime_header("=?utf-8?Q?Hello=0A=20World?=")
        self.assertEqual(decoded, "Hello World")


class ExtractFullBodyTestCase(unittest.TestCase):
    def test_html_only_mail_extracted_to_full_readable_text(self):
        html = (
            "<html><body>"
            "<h1>Willkommen</h1>"
            "<p>Dies ist ein <b>vollstaendiger</b> Text mit mehr als 280 Zeichen. "
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
            "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, "
            "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.</p>"
            "<img src='https://tracker.example/pixel.gif' alt=''>"
            "</body></html>"
        )
        msg = reload_message(make_html_only_message(subject="HTML Mail", from_addr="a@example.com", html=html))
        text, links = extract_full_body(msg)

        self.assertIn("Willkommen", text)
        self.assertIn("Lorem ipsum", text)
        self.assertGreater(len(text), 280)
        self.assertNotIn("tracker.example", text)  # no remote-image/tracking-pixel carryover
        self.assertEqual(links, [])

    def test_links_extracted_with_correct_anchor_text(self):
        html = (
            "<html><body><p>Siehe:</p>"
            "<a href='https://example.com/a'>Erster Link</a>"
            "<a href='https://example.com/b'>Zweiter Link</a>"
            "</body></html>"
        )
        msg = reload_message(make_html_only_message(subject="Links", from_addr="a@example.com", html=html))
        _text, links = extract_full_body(msg)

        self.assertEqual(
            links,
            [("Erster Link", "https://example.com/a"), ("Zweiter Link", "https://example.com/b")],
        )

    def test_plain_text_mail_preferred_over_html_alternative(self):
        msg = make_plain_message(subject="Plain", from_addr="a@example.com", body="Reiner Text ohne HTML.")
        text, links = extract_full_body(reload_message(msg))
        self.assertEqual(text, "Reiner Text ohne HTML.")
        self.assertEqual(links, [])


class ExtractAttachmentsTestCase(unittest.TestCase):
    def test_attachment_below_cap_reported_with_correct_size(self):
        msg = make_plain_message(subject="Mit Anhang", from_addr="a@example.com", body="siehe Anhang")
        add_attachment(msg, "lebenslauf.pdf", b"%PDF-1.4 fake content", mime_type="application/pdf")
        msg = reload_message(msg)

        attachments = extract_attachments(msg)

        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].filename, "lebenslauf.pdf")
        self.assertEqual(attachments[0].mime_type, "application/pdf")
        self.assertEqual(attachments[0].size_bytes, len(b"%PDF-1.4 fake content"))

    def test_multiple_attachments_all_extracted(self):
        msg = make_plain_message(subject="Mehrere Anhaenge", from_addr="a@example.com", body="siehe Anhaenge")
        add_attachment(msg, "a.pdf", b"a" * 100, mime_type="application/pdf")
        add_attachment(msg, "b.jpg", b"b" * 5_000_000, mime_type="image/jpeg")
        msg = reload_message(msg)

        attachments = extract_attachments(msg)

        self.assertEqual([a.filename for a in attachments], ["a.pdf", "b.jpg"])
        self.assertEqual(attachments[1].size_bytes, 5_000_000)


if __name__ == "__main__":
    unittest.main()
