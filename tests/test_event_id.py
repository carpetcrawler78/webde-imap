import unittest

from webde_imap.event_id import compute_event_id


class ComputeEventIdTestCase(unittest.TestCase):
    def test_deterministic_for_same_inputs(self):
        a = compute_event_id("user@web.de", "INBOX", 1000, 42, "<msg@example.com>")
        b = compute_event_id("user@web.de", "INBOX", 1000, 42, "<msg@example.com>")
        self.assertEqual(a, b)

    def test_differs_across_uidvalidity(self):
        before = compute_event_id("user@web.de", "INBOX", 1000, 42, "<msg@example.com>")
        after = compute_event_id("user@web.de", "INBOX", 2000, 42, "<msg@example.com>")
        self.assertNotEqual(before, after)

    def test_differs_across_uid(self):
        first = compute_event_id("user@web.de", "INBOX", 1000, 42, None)
        second = compute_event_id("user@web.de", "INBOX", 1000, 43, None)
        self.assertNotEqual(first, second)

    def test_stable_without_message_id(self):
        a = compute_event_id("user@web.de", "INBOX", 1000, 42, None)
        b = compute_event_id("user@web.de", "INBOX", 1000, 42, "")
        self.assertEqual(a, b)

    def test_starts_with_webde_prefix(self):
        event_id = compute_event_id("user@web.de", "INBOX", 1000, 42, None)
        self.assertTrue(event_id.startswith("webde-"))


if __name__ == "__main__":
    unittest.main()
