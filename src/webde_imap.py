import imaplib
import json
import os
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header
from email.message import Message

STATE_NAMESPACE = "WEBDE_IMAP"
INBOX_STATE_KEY = "inbox_last_uid"
SENT_STATE_KEY = "sent_last_uid"
LAST_RUN_AT_STATE_KEY = "last_run_at"
DEFAULT_EXCERPT_LENGTH = 280


def load_config():
    return {
        "imap_server": os.environ["WEBDE_IMAP_SERVER"],
        "username": os.environ["WEBDE_IMAP_USERNAME"],
        "password": os.environ["WEBDE_IMAP_PASSWORD"],
        "inbox_folder": os.environ.get("WEBDE_IMAP_INBOX_FOLDER", "INBOX"),
        "sent_folder": os.environ.get("WEBDE_IMAP_SENT_FOLDER", "Sent"),
        "excerpt_length": int(os.environ.get("WEBDE_IMAP_EXCERPT_LENGTH", DEFAULT_EXCERPT_LENGTH)),
    }


def connect_to_imap(config):
    try:
        mail = imaplib.IMAP4_SSL(config["imap_server"])
        mail.login(config["username"], config["password"])
        return mail
    except Exception as exc:
        print(f"Failed to connect to IMAP server: {exc}")
        return None


def load_state():
    raw_state = os.environ.get("SCHEDULER_STATE", "").strip()
    if not raw_state:
        return {STATE_NAMESPACE: {}}

    try:
        parsed_state = json.loads(raw_state)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SCHEDULER_STATE is not valid JSON: {exc}") from exc

    if not isinstance(parsed_state, dict):
        raise ValueError("SCHEDULER_STATE must decode to a JSON object.")

    namespace_state = parsed_state.get(STATE_NAMESPACE)
    if namespace_state is None:
        parsed_state[STATE_NAMESPACE] = {}
    elif not isinstance(namespace_state, dict):
        raise ValueError(f"SCHEDULER_STATE['{STATE_NAMESPACE}'] must be a JSON object.")

    return parsed_state


def get_state_value(state, key):
    return state[STATE_NAMESPACE].get(key)


def set_state_value(state, key, value):
    state[STATE_NAMESPACE][key] = value


def write_action_output(name, value):
    github_output_path = os.environ.get("GITHUB_OUTPUT")
    if github_output_path:
        with open(github_output_path, "a", encoding="utf-8") as output_file:
            output_file.write(f"{name}<<__CODEX_EOF__\n{value}\n__CODEX_EOF__\n")
    else:
        print(f"{name}={value}")


def get_highest_uid(mail, folder):
    status, _ = mail.select(folder, readonly=True)
    if status != "OK":
        raise RuntimeError(f"Could not select folder '{folder}' in read-only mode.")

    status, data = mail.uid("SEARCH", None, "ALL")
    if status != "OK":
        raise RuntimeError(f"UID SEARCH failed for folder '{folder}'.")

    uid_list = data[0].split() if data and data[0] else []
    if not uid_list:
        return 0

    return int(uid_list[-1])


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


def extract_text_excerpt(message, excerpt_length):
    body_parts = []

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue

            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition.lower():
                continue

            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                body_parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = message.get_payload(decode=True) or b""
        charset = message.get_content_charset() or "utf-8"
        body_parts.append(payload.decode(charset, errors="replace"))

    normalized = " ".join(" ".join(body_parts).split())
    return normalized[:excerpt_length]


def build_row(folder, uid, message_obj, excerpt_length):
    return {
        "folder": folder,
        "uid": uid,
        "from": decode_mime_header(message_obj.get("From")),
        "to": decode_mime_header(message_obj.get("To")),
        "subject": decode_mime_header(message_obj.get("Subject")),
        "excerpt": extract_text_excerpt(message_obj, excerpt_length),
    }


def fetch_message_by_uid(mail, uid):
    status, msg_data = mail.uid("FETCH", str(uid), "(RFC822)")
    if status != "OK":
        raise RuntimeError(f"UID FETCH failed for UID {uid}.")

    for item in msg_data:
        if isinstance(item, tuple):
            return message_from_bytes(item[1])

    raise RuntimeError(f"No RFC822 payload returned for UID {uid}.")


def process_folder(mail, folder, last_uid, excerpt_length):
    status, _ = mail.select(folder, readonly=True)
    if status != "OK":
        raise RuntimeError(f"Could not select folder '{folder}' in read-only mode.")

    status, data = mail.uid("SEARCH", None, f"(UID {last_uid + 1}:*)")
    if status != "OK":
        raise RuntimeError(f"UID SEARCH failed for folder '{folder}'.")

    uid_list = [int(uid) for uid in (data[0].split() if data and data[0] else [])]
    if not uid_list:
        return [], last_uid

    rows = []
    for uid in uid_list:
        message_obj = fetch_message_by_uid(mail, uid)
        rows.append(build_row(folder, uid, message_obj, excerpt_length))

    return rows, uid_list[-1]


def write_output(rows):
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    write_action_output("WEBDE_MAIL", payload)


def save_state(state):
    write_action_output("SCHEDULER_STATE", json.dumps(state, ensure_ascii=False))


def process_folder_with_bootstrap(mail, folder, state, state_key, excerpt_length):
    last_uid = get_state_value(state, state_key)

    if last_uid is None:
        highest_uid = get_highest_uid(mail, folder)
        set_state_value(state, state_key, highest_uid)
        print(f"Bootstrapped folder '{folder}' with last_uid={highest_uid}")
        return []

    rows, new_last_uid = process_folder(mail, folder, int(last_uid), excerpt_length)
    set_state_value(state, state_key, new_last_uid)
    print(f"Processed folder '{folder}' with {len(rows)} new message(s); last_uid={new_last_uid}")
    return rows


def main():
    config = load_config()
    state = load_state()
    mail = connect_to_imap(config)

    if not mail:
        raise SystemExit(1)

    all_rows = []

    try:
        folder_configs = [
            (config["inbox_folder"], INBOX_STATE_KEY),
            (config["sent_folder"], SENT_STATE_KEY),
        ]

        for folder, state_key in folder_configs:
            all_rows.extend(
                process_folder_with_bootstrap(
                    mail=mail,
                    folder=folder,
                    state=state,
                    state_key=state_key,
                    excerpt_length=config["excerpt_length"],
                )
            )

        set_state_value(
            state,
            LAST_RUN_AT_STATE_KEY,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        )
        write_output(all_rows)
        save_state(state)
    finally:
        mail.logout()


if __name__ == "__main__":
    main()
