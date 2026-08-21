import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from .mail_content import decode_mime_header


def _build_header_block(category, event_id, folder, uid, message):
    lines = [
        f"WEBDE_EVENT_ID: {event_id}",
        f"WEBDE_CATEGORY: {category}",
        f"ORIGINAL_FOLDER: {folder}",
        f"ORIGINAL_UID: {uid}",
        f"ORIGINAL_MESSAGE_ID: {message.get('Message-Id', '')}",
        f"ORIGINAL_FROM: {decode_mime_header(message.get('From'))}",
        f"ORIGINAL_TO: {decode_mime_header(message.get('To'))}",
    ]
    reply_to = decode_mime_header(message.get("Reply-To"))
    if reply_to:
        lines.append(f"ORIGINAL_REPLY_TO: {reply_to}")
    lines.append(f"ORIGINAL_DATE: {message.get('Date', '')}")
    lines.append(f"FETCHED_AT_UTC: {datetime.now(timezone.utc).replace(microsecond=0).isoformat()}")
    return "\n".join(lines)


def _build_links_section(links):
    if not links:
        return "Links: (keine)"
    lines = ["Links:"]
    for text, href in links:
        lines.append(f"- {text}: {href}")
    return "\n".join(lines)


def _build_attachments_section(forwarded, skipped):
    if not forwarded and not skipped:
        return "Anhaenge: (keine)"
    lines = ["Anhaenge:"]
    for att in forwarded:
        lines.append(f"- {att.filename} ({att.mime_type}, {att.size_bytes} bytes)")
    for att, reason in skipped:
        lines.append(f"- {att.filename} ({att.mime_type}, {att.size_bytes} bytes) -- {reason}")
    return "\n".join(lines)


def select_attachments_within_cap(attachments, cap_bytes):
    forwarded, skipped = [], []
    running_total = 0
    for att in attachments:
        if att.payload_bytes is None:
            skipped.append((att, "nicht weitergeleitet (technisch nicht verarbeitbar)"))
            continue
        if running_total + att.size_bytes > cap_bytes:
            skipped.append((att, "nicht weitergeleitet (Groessenlimit ueberschritten)"))
            continue
        running_total += att.size_bytes
        forwarded.append(att)
    return forwarded, skipped


def build_forward_message(message, content, category, event_id, folder, uid, config):
    forwarded_attachments, skipped_attachments = select_attachments_within_cap(
        content.attachments, config["max_attachment_total_bytes"]
    )

    body_text = content.body_text or "(kein Text extrahierbar)"
    max_chars = config.get("max_body_chars")
    if max_chars and len(body_text) > max_chars:
        body_text = body_text[:max_chars] + "\n[... gekuerzt ...]"

    body = "\n\n".join(
        [
            _build_header_block(category, event_id, folder, uid, message),
            body_text,
            _build_links_section(content.links),
            _build_attachments_section(forwarded_attachments, skipped_attachments),
        ]
    )

    forward_msg = EmailMessage()
    forward_msg["Subject"] = f"[WEBDE][{category}] {decode_mime_header(message.get('Subject'))}"
    forward_msg["From"] = config["smtp_username"]
    forward_msg["To"] = config["forward_to"]
    forward_msg.set_content(body)

    for att in forwarded_attachments:
        maintype, _, subtype = (att.mime_type or "application/octet-stream").partition("/")
        forward_msg.add_attachment(
            att.payload_bytes,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=att.filename,
        )

    return forward_msg


def send_via_smtp(forward_msg, config):
    with smtplib.SMTP(config["smtp_server"], config["smtp_port"], timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config["smtp_username"], config["smtp_password"])
        smtp.send_message(forward_msg)
