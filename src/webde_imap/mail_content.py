import dataclasses
from email.header import decode_header
from html.parser import HTMLParser
from typing import List, Optional, Tuple

BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote"}
SKIP_TAGS = {"script", "style", "head", "title"}


def decode_mime_header(value):
    if not value:
        return ""

    decoded_parts = []
    for chunk, encoding in decode_header(value):
        if isinstance(chunk, bytes):
            decoded_parts.append(chunk.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded_parts.append(chunk)

    return "".join(decoded_parts).strip()


class TextAndLinksExtractor(HTMLParser):
    """Strips HTML down to readable plain text + a separate (anchor_text, href) link list.

    Never carries over image `src` (no tracking pixels / remote images) or any
    script/style content -- only visible text and link targets survive.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._anchor_href = None
        self._anchor_text_parts = []
        self._body_parts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "a" and attrs_dict.get("href"):
            self._anchor_href = attrs_dict["href"]
            self._anchor_text_parts = []
        elif tag == "img":
            alt = (attrs_dict.get("alt") or "").strip()
            if alt:
                self._body_parts.append(f"[Bild: {alt}]")
        elif tag in BLOCK_TAGS:
            self._body_parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "a" and self._anchor_href is not None:
            text = "".join(self._anchor_text_parts).strip()
            self.links.append((text or self._anchor_href, self._anchor_href))
            if text:
                self._body_parts.append(text)
            self._anchor_href = None
            self._anchor_text_parts = []
        elif tag in BLOCK_TAGS:
            self._body_parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._anchor_href is not None:
            self._anchor_text_parts.append(data)
        else:
            self._body_parts.append(data)

    def get_text(self):
        raw = "".join(self._body_parts)
        lines = [line.strip() for line in raw.splitlines()]
        collapsed = []
        blank = False
        for line in lines:
            if line:
                collapsed.append(line)
                blank = False
            elif not blank:
                collapsed.append("")
                blank = True
        return "\n".join(collapsed).strip()


def html_to_text_and_links(html):
    parser = TextAndLinksExtractor()
    parser.feed(html)
    parser.close()
    return parser.get_text(), parser.links


def _decode_payload(part):
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def extract_full_body(message) -> Tuple[str, List[Tuple[str, str]]]:
    plain_parts = []
    html_parts = []

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition.lower():
                continue
            if part.get_content_type() == "text/plain":
                plain_parts.append(_decode_payload(part))
            elif part.get_content_type() == "text/html":
                html_parts.append(_decode_payload(part))
    elif message.get_content_type() == "text/html":
        html_parts.append(_decode_payload(message))
    else:
        plain_parts.append(_decode_payload(message))

    if plain_parts:
        return "\n\n".join(plain_parts).strip(), []
    if html_parts:
        return html_to_text_and_links("\n".join(html_parts))
    return "", []


@dataclasses.dataclass
class AttachmentInfo:
    filename: str
    mime_type: str
    size_bytes: int
    payload_bytes: Optional[bytes]


def extract_attachments(message) -> List[AttachmentInfo]:
    attachments = []
    if not message.is_multipart():
        return attachments

    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = str(part.get("Content-Disposition", ""))
        filename = part.get_filename()
        is_attachment = "attachment" in disposition.lower() or bool(filename)
        if not is_attachment:
            continue

        decoded_filename = decode_mime_header(filename) if filename else "unbenannt"
        payload = part.get_payload(decode=True)
        size = len(payload) if payload is not None else 0
        attachments.append(
            AttachmentInfo(
                filename=decoded_filename,
                mime_type=part.get_content_type() or "application/octet-stream",
                size_bytes=size,
                payload_bytes=payload,
            )
        )
    return attachments


@dataclasses.dataclass
class MessageContent:
    body_text: str
    links: List[Tuple[str, str]]
    attachments: List[AttachmentInfo]


def extract_message_content(message) -> MessageContent:
    body_text, links = extract_full_body(message)
    attachments = extract_attachments(message)
    return MessageContent(body_text=body_text, links=links, attachments=attachments)
