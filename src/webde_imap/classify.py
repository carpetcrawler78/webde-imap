import re
from email.utils import parseaddr

from .mail_content import decode_mime_header

CATEGORIES = ("JOB", "MESSAGE", "APPLICATION", "IMPORTANT", "IGNORE", "REVIEW")


def _keyword_pattern(keyword):
    """Word-boundary regex for a keyword/phrase.

    Plain substring matching lets keywords fire inside unrelated German compound
    words (e.g. "kuendigung" inside "ankuendigung"). \\b only applies where the
    keyword actually starts/ends on a word character, so punctuation-leading
    keywords like "% off" still match as plain substrings at that end.
    """
    escaped = re.escape(keyword)
    prefix = r"\b" if keyword[:1].isalnum() else ""
    suffix = r"\b" if keyword[-1:].isalnum() else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def _matches_any(haystack, needles):
    if not haystack:
        return False
    return any(_keyword_pattern(needle).search(haystack) for needle in needles)


def _sender_parts(message):
    _, addr = parseaddr(message.get("From", ""))
    addr = addr.lower()
    if "@" not in addr:
        return "", ""
    local_part, domain = addr.split("@", 1)
    return local_part, domain


def _match_platform_group(message, subject_lower, body_lower, group):
    local_part, domain = _sender_parts(message)
    list_id = decode_mime_header(message.get("List-Id")).lower()

    for platform in group:
        if domain and any(d.lower() in domain for d in platform.get("sender_domains", [])):
            return True
        if local_part and any(lp.lower() in local_part for lp in platform.get("sender_local_parts", [])):
            return True
        if list_id and any(p.lower() in list_id for p in platform.get("list_id_patterns", [])):
            return True
        if _matches_any(subject_lower, platform.get("subject_keywords", [])):
            return True
        if _matches_any(body_lower, platform.get("subject_keywords", [])):
            return True
    return False


def _is_known_noise_sender(message, ignore_signals):
    """Hard-blocked senders (e.g. finance-advice newsletters, association mailings) --
    always IGNORE regardless of content, no topic exception."""
    local_part, domain = _sender_parts(message)
    ignore_domains = ignore_signals.get("sender_domains", [])
    ignore_local_parts = ignore_signals.get("sender_local_parts", [])
    return (domain and any(d.lower() in domain for d in ignore_domains)) or (
        local_part and any(lp.lower() in local_part for lp in ignore_local_parts)
    )


def _is_topic_override_sender(message, ignore_signals):
    """News/media outlets that are noise by default, but should NOT be ignored
    when a specific edition is dedicated to an AI/data-science-relevant topic."""
    _, domain = _sender_parts(message)
    override_domains = ignore_signals.get("topic_override_sender_domains", [])
    return bool(domain) and any(d.lower() in domain for d in override_domains)


def _has_topic_override_keyword(subject_lower, body_lower, ignore_signals):
    topic_keywords = ignore_signals.get("topic_override_keywords", [])
    return _matches_any(subject_lower, topic_keywords) or _matches_any(body_lower, topic_keywords)


def classify_message(message, content, rules):
    """Deterministic, config-driven classification. No LLM/API calls.

    Precedence (short-circuit): JOB > MESSAGE > hard-noise-sender IGNORE >
    news-outlet IGNORE (unless AI/data-science topic override) > APPLICATION >
    IMPORTANT > generic-keyword IGNORE > REVIEW.

    Job/message platform allowlists are checked before any IGNORE check, so a
    job digest with newsletter headers still resolves to JOB, not IGNORE.

    Sender-identity IGNORE checks (ignore_signals.sender_domains/sender_local_parts
    and topic_override_sender_domains) run *before* the generic APPLICATION/
    IMPORTANT body-keyword scan: a specific newsletter's sender identity is a far
    more precise signal than a single word like "Interview" or "Rechnung"
    appearing incidentally in that newsletter's prose, so it must win to avoid
    mislabeling known noise as APPLICATION/IMPORTANT.

    News/media outlets in topic_override_sender_domains (e.g. Spiegel, Tagesspiegel
    Background) are noise by default, but a specific edition mentioning an
    AI/data-science keyword (topic_override_keywords) is let through instead of
    being ignored -- generic news is out, dedicated AI/data-science coverage is not.

    The generic subject/body IGNORE keywords (e.g. "Rabatt", "Sale") stay checked
    last, after APPLICATION/IMPORTANT, since they are not sender-specific and a
    genuinely important mail could plausibly contain one incidentally.

    Anything not confidently matched defaults to REVIEW, never to IGNORE.
    """
    subject_lower = decode_mime_header(message.get("Subject")).lower()
    body_lower = (content.body_text or "").lower()
    has_list_unsubscribe = bool(message.get("List-Unsubscribe"))
    ignore_signals = rules.get("ignore_signals", {})

    if _match_platform_group(message, subject_lower, body_lower, rules.get("job_platforms", [])):
        return "JOB"

    if _match_platform_group(message, subject_lower, body_lower, rules.get("message_platforms", [])):
        return "MESSAGE"

    if _is_known_noise_sender(message, ignore_signals):
        # No List-Unsubscribe gate here: this list is hand-curated from reviewed
        # real mail (unlike the generic keyword list below), and some legitimate
        # newsletters (e.g. association/club mailings) omit that header entirely.
        return "IGNORE"

    is_topic_override_sender = _is_topic_override_sender(message, ignore_signals)
    has_topic_override = _has_topic_override_keyword(subject_lower, body_lower, ignore_signals)
    if is_topic_override_sender and not has_topic_override:
        return "IGNORE"

    application_keywords = rules.get("application_keywords", {}).get("subject_or_body", [])
    application_subject_hit = _matches_any(subject_lower, application_keywords)
    application_body_hit = _matches_any(body_lower, application_keywords)
    # Newsletter prose often mentions recruiting concepts incidentally. Keep an
    # explicit subject match, but do not promote a list mail on a body-only hit.
    if application_subject_hit or (application_body_hit and not has_list_unsubscribe):
        return "APPLICATION"

    important_keywords = rules.get("important_keywords", {}).get("subject_or_body", [])
    if _matches_any(subject_lower, important_keywords) or _matches_any(body_lower, important_keywords):
        return "IMPORTANT"

    ignore_keywords = ignore_signals.get("subject_or_body_keywords", [])
    keyword_hit = _matches_any(subject_lower, ignore_keywords) or _matches_any(body_lower, ignore_keywords)
    if has_list_unsubscribe and keyword_hit and not (is_topic_override_sender and has_topic_override):
        return "IGNORE"

    return "REVIEW"
