import json
import unittest
from pathlib import Path

from webde_imap.classify import classify_message
from webde_imap.mail_content import extract_message_content

from helpers import make_html_alternative_message, make_plain_message, reload_message

RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "routing_rules.json"


class ClassifyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(RULES_PATH, "r", encoding="utf-8") as rules_file:
            cls.rules = json.load(rules_file)

    def _classify(self, msg):
        msg = reload_message(msg)
        content = extract_message_content(msg)
        return classify_message(msg, content, self.rules)

    def test_linkedin_job_alert_classified_as_job(self):
        msg = make_plain_message(
            subject="Job Alert: 25 neue Jobs für dich",
            from_addr="jobalerts-noreply@linkedin.com",
            body="Data Engineer bei Beispiel GmbH",
            list_unsubscribe="<https://www.linkedin.com/unsubscribe>",
        )
        self.assertEqual(self._classify(msg), "JOB")

    def test_xing_job_digest_multiple_links_classified_as_job(self):
        html = (
            "<html><body><p>Neue Jobs für dich</p>"
            "<a href='https://xing.com/jobs/1'>Data Scientist</a>"
            "<a href='https://xing.com/jobs/2'>ML Engineer</a>"
            "</body></html>"
        )
        msg = make_html_alternative_message(
            subject="XING Jobagent: neue Stellenangebote",
            from_addr="jobagent@xing.com",
            html=html,
            list_unsubscribe="<https://xing.com/unsubscribe>",
        )
        self.assertEqual(self._classify(msg), "JOB")

    def test_linkedin_direct_message_classified_as_message(self):
        msg = make_plain_message(
            subject="Anna Muster hat Ihnen eine Nachricht gesendet",
            from_addr="messaging-digest-noreply@linkedin.com",
            body="Hallo, ich wollte mich zu Ihrem Profil melden.",
        )
        self.assertEqual(self._classify(msg), "MESSAGE")

    def test_application_rejection_classified_as_application(self):
        msg = make_plain_message(
            subject="Ihre Bewerbung bei Beispiel GmbH",
            from_addr="recruiting@beispiel-gmbh.de",
            body="Leider müssen wir Ihnen mitteilen, dass wir uns für eine Absage entschieden haben.",
        )
        self.assertEqual(self._classify(msg), "APPLICATION")

    def test_application_interview_invite_classified_as_application(self):
        msg = make_plain_message(
            subject="Einladung zum Vorstellungsgespräch",
            from_addr="hr@beispiel-gmbh.de",
            body="Wir laden Sie herzlich zum Interview am kommenden Montag ein.",
        )
        self.assertEqual(self._classify(msg), "APPLICATION")

    def test_security_deadline_mail_classified_as_important(self):
        msg = make_plain_message(
            subject="Sicherheitswarnung: neue Anmeldung erkannt",
            from_addr="security@example-bank.de",
            body="Wir haben eine neue Anmeldung zu Ihrem Konto festgestellt.",
        )
        self.assertEqual(self._classify(msg), "IMPORTANT")

    def test_ambiguous_personal_mail_defaults_to_review_not_ignore(self):
        msg = make_plain_message(
            subject="Kurze Frage",
            from_addr="freund@example.com",
            body="Hast du am Wochenende Zeit fuer einen Kaffee?",
        )
        self.assertEqual(self._classify(msg), "REVIEW")

    def test_unambiguous_marketing_noise_classified_as_ignore(self):
        msg = make_plain_message(
            subject="Black Friday Sale: 30% Rabatt auf alles!",
            from_addr="newsletter@online-shop.example",
            body="Jetzt einkaufen und sparen. Gutschein-Code: SAVE30.",
            list_unsubscribe="<https://online-shop.example/unsubscribe>",
        )
        self.assertEqual(self._classify(msg), "IGNORE")

    def test_known_generic_newsletter_ignored_despite_coincidental_keyword_hit(self):
        # A finance-advice newsletter that happens to mention "Rechnung"/"Kuendigung"
        # in a generic example, not the user's own -- known-noise-domain + List-Unsubscribe
        # must route this to IGNORE instead of a coincidental IMPORTANT/APPLICATION hit.
        msg = make_plain_message(
            subject="Tagesgeld-Rekord im Check",
            from_addr="newsletter@finanztip.de",
            body="So sieht die Rechnung aus, und der Kuendigungsservice erledigt das fuer dich.",
            list_unsubscribe="<https://finanztip.de/unsubscribe>",
        )
        self.assertEqual(self._classify(msg), "IGNORE")

    def test_news_outlet_ignored_by_default(self):
        msg = make_plain_message(
            subject="Zerreisst es die CDU?",
            from_addr="newsletter@angebote.spiegel.de",
            body="Die politische Lage am Morgen, ganz ohne Technologiethema.",
            list_unsubscribe="<https://spiegel.de/unsubscribe>",
        )
        self.assertEqual(self._classify(msg), "IGNORE")

    def test_news_outlet_with_ai_topic_is_not_ignored(self):
        # Same outlet as above, but this specific edition is dedicated to AI --
        # must NOT be swallowed as generic news noise.
        msg = make_plain_message(
            subject="KI-Airbus: Braucht es einen neuen Anlauf?",
            from_addr="noreply@background.tagesspiegel.de",
            body="Ein Ueberblick zur Debatte um Kuenstliche Intelligenz in der Industriepolitik.",
            list_unsubscribe="<https://background.tagesspiegel.de/unsubscribe>",
        )
        self.assertEqual(self._classify(msg), "REVIEW")

    def test_finance_newsletter_stays_ignored_even_with_ai_mention(self):
        # Finanztip is a hard-blocked sender (not a news outlet) -- a passing "KI"
        # mention among unrelated finance bullet points must not rescue it.
        msg = make_plain_message(
            subject="Tagesgeld-Rekord im Check ++ KI-Blase ++ Gaspreis-Hoch",
            from_addr="newsletter@finanztip.de",
            body="Zinsen, Gaspreise und eine kurze Erwaehnung der KI-Blase an den Maerkten.",
            list_unsubscribe="<https://finanztip.de/unsubscribe>",
        )
        self.assertEqual(self._classify(msg), "IGNORE")

    def test_known_noise_sender_ignored_even_without_list_unsubscribe_header(self):
        # Real-world case: some association newsletters (e.g. VdK) omit
        # List-Unsubscribe entirely -- the curated sender list must not depend on it.
        msg = make_plain_message(
            subject="Ihre digitale Zeitung - Ausgabe September",
            from_addr="baden-wuerttemberg@e-zeitung.vdk.de",
            body="Ein Mitglied erklaert im Interview, warum ihr das wichtig ist.",
        )
        self.assertEqual(self._classify(msg), "IGNORE")

    def test_known_newsletter_sender_local_part_ignored(self):
        msg = make_plain_message(
            subject="Laesst sich die Persoenlichkeit veraendern?",
            from_addr="dastutmirgut@zeit.de",
            body="Ein Interview zum Thema Persoenlichkeitsentwicklung.",
            list_unsubscribe="<https://zeit.de/unsubscribe>",
        )
        self.assertEqual(self._classify(msg), "IGNORE")

    def test_job_allowlist_overrides_newsletter_heuristic(self):
        # Job alerts also carry List-Unsubscribe headers -- the job-platform match
        # must win before the generic newsletter/IGNORE check is ever reached.
        msg = make_plain_message(
            subject="Job Alert: neue Jobs für dich",
            from_addr="jobs-noreply@linkedin.com",
            body="Rabatt Sale % off",  # deliberately also contains ignore-keywords
            list_unsubscribe="<https://www.linkedin.com/unsubscribe>",
        )
        self.assertEqual(self._classify(msg), "JOB")


if __name__ == "__main__":
    unittest.main()
