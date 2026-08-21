from email import message_from_bytes
from email.message import EmailMessage


def _set_common_headers(msg, subject, from_addr, to_addr, message_id, list_unsubscribe, extra_headers):
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Message-Id"] = message_id
    msg["Date"] = "Fri, 21 Aug 2026 10:00:00 +0000"
    if list_unsubscribe:
        msg["List-Unsubscribe"] = list_unsubscribe
    if extra_headers:
        for key, value in extra_headers.items():
            msg[key] = value


def make_plain_message(
    subject,
    from_addr,
    body="",
    to_addr="user@web.de",
    message_id="<test@example.com>",
    list_unsubscribe=None,
    extra_headers=None,
):
    msg = EmailMessage()
    _set_common_headers(msg, subject, from_addr, to_addr, message_id, list_unsubscribe, extra_headers)
    msg.set_content(body)
    return msg


def make_html_only_message(
    subject,
    from_addr,
    html,
    to_addr="user@web.de",
    message_id="<test@example.com>",
    list_unsubscribe=None,
    extra_headers=None,
):
    """A message with ONLY a text/html part -- no text/plain alternative."""
    msg = EmailMessage()
    _set_common_headers(msg, subject, from_addr, to_addr, message_id, list_unsubscribe, extra_headers)
    msg.set_content(html, subtype="html")
    return msg


def make_html_alternative_message(
    subject,
    from_addr,
    html,
    plain="Bitte HTML-faehigen Client verwenden.",
    to_addr="user@web.de",
    message_id="<test@example.com>",
    list_unsubscribe=None,
    extra_headers=None,
):
    """A multipart/alternative message with both text/plain and text/html."""
    msg = EmailMessage()
    _set_common_headers(msg, subject, from_addr, to_addr, message_id, list_unsubscribe, extra_headers)
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")
    return msg


def add_attachment(msg, filename, content_bytes, mime_type="application/octet-stream"):
    maintype, _, subtype = mime_type.partition("/")
    msg.add_attachment(content_bytes, maintype=maintype, subtype=subtype, filename=filename)
    return msg


def reload_message(msg):
    """Round-trips through raw bytes, like a real IMAP RFC822 FETCH would produce."""
    return message_from_bytes(msg.as_bytes())
