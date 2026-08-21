import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_ATTACHMENT_CAP_BYTES = 10 * 1024 * 1024
DEFAULT_ROUTING_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "routing_rules.json"


def load_config():
    return {
        "imap_server": os.environ["WEBDE_IMAP_SERVER"],
        "imap_username": os.environ["WEBDE_IMAP_USERNAME"],
        "imap_password": os.environ["WEBDE_IMAP_PASSWORD"],
        "inbox_folder": os.environ.get("WEBDE_IMAP_INBOX_FOLDER") or "INBOX",
        "smtp_server": os.environ.get("WEBDE_SMTP_SERVER"),
        "smtp_port": int(os.environ.get("WEBDE_SMTP_PORT") or 587),
        "smtp_username": os.environ.get("WEBDE_SMTP_USERNAME"),
        "smtp_password": os.environ.get("WEBDE_SMTP_PASSWORD"),
        "forward_to": os.environ.get("WEBDE_FORWARD_TO"),
        "max_attachment_total_bytes": int(
            os.environ.get("WEBDE_MAX_ATTACHMENT_TOTAL_BYTES") or DEFAULT_ATTACHMENT_CAP_BYTES
        ),
        "max_body_chars": (
            int(os.environ["WEBDE_MAX_BODY_CHARS"]) if os.environ.get("WEBDE_MAX_BODY_CHARS") else None
        ),
    }


def require_smtp_config(config):
    missing = [key for key in ("smtp_server", "smtp_username", "smtp_password", "forward_to") if not config.get(key)]
    if missing:
        raise RuntimeError(f"Missing required SMTP configuration: {', '.join(missing)}")


def load_routing_rules(path=None):
    rules_path = Path(path) if path else DEFAULT_ROUTING_RULES_PATH
    with open(rules_path, "r", encoding="utf-8") as rules_file:
        return json.load(rules_file)
