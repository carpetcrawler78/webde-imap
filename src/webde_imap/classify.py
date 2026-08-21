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


def classify_message(message, content, rules):
    """Deterministic, config-driven classification. No LLM/API calls.

    Precedence (short-circuit): JOB > MESSAGE > APPLICATION > IMPORTANT > IGNORE > REVIEW.
    Job/message platform allowlists are checked before the List-Unsubscribe/IGNORE
    check, so a job digest with newsletter headers still resolves to JOB, not IGNORE.
    Anything not confidently matched defaults to REVIEW, never to IGNORE.
    """
    subject_lower = decode_mime_header(message.get("Subject")).lower()
    body_lower = (content.body_text or "").lower()

    if _match_platform_group(message, subject_lower, body_lower, rules.get("job_platforms", [])):
        return "JOB"

    if _match_platform_group(message, subject_lower, body_lower, rules.get("message_platforms", [])):
        return "MESSAGE"

    application_keywords = rules.get("application_keywords", {}).get("subject_or_body", [])
    if _matches_any(subject_lower, application_keywords) or _matches_any(body_lower, application_keywords):
        return "APPLICATION"

    important_keywords = rules.get("important_keywords", {}).get("subject_or_body", [])
    if _matches_any(subject_lower, important_keywords) or _matches_any(body_lower, important_keywords):
        return "IMPORTANT"

    has_list_unsubscribe = bool(message.get("List-Unsubscribe"))
    ignore_keywords = rules.get("ignore_signals", {}).get("subject_or_body_keywords", [])
    if has_list_unsubscribe and (_matches_any(subject_lower, ignore_keywords) or _matches_any(body_lower, ignore_keywords)):
        return "IGNORE"

    return "REVIEW"
