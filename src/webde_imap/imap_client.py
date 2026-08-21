import imaplib
import re
from datetime import datetime, timedelta, timezone
from email import message_from_bytes

UIDVALIDITY_PATTERN = re.compile(rb"UIDVALIDITY (\d+)")


def connect(config):
    mail = imaplib.IMAP4_SSL(config["imap_server"])
    mail.login(config["imap_username"], config["imap_password"])
    return mail


def logout(mail):
    try:
        mail.logout()
    except Exception:
        pass


def get_uidvalidity(mail, folder):
    status, data = mail.status(folder, "(UIDVALIDITY)")
    if status != "OK" or not data or not data[0]:
        raise RuntimeError(f"STATUS UIDVALIDITY failed for folder '{folder}'.")
    match = UIDVALIDITY_PATTERN.search(data[0])
    if not match:
        raise RuntimeError(f"Could not parse UIDVALIDITY response for folder '{folder}'.")
    return int(match.group(1))


def select_readonly(mail, folder):
    status, _ = mail.select(folder, readonly=True)
    if status != "OK":
        raise RuntimeError(f"Could not select folder '{folder}' in read-only mode.")


def get_highest_uid(mail, folder):
    select_readonly(mail, folder)
    status, data = mail.uid("SEARCH", None, "ALL")
    if status != "OK":
        raise RuntimeError(f"UID SEARCH failed for folder '{folder}'.")
    uid_list = data[0].split() if data and data[0] else []
    return int(uid_list[-1]) if uid_list else 0


def list_uids_after(mail, folder, last_uid):
    select_readonly(mail, folder)
    status, data = mail.uid("SEARCH", None, f"(UID {last_uid + 1}:*)")
    if status != "OK":
        raise RuntimeError(f"UID SEARCH failed for folder '{folder}'.")
    uid_list = sorted(int(uid) for uid in (data[0].split() if data and data[0] else []))
    # imaplib quirk: "UID x:*" can return the highest existing UID even when x is beyond it.
    return [uid for uid in uid_list if uid > last_uid]


def list_uids_for_bootstrap(mail, folder, last_n=None, window_hours=None):
    select_readonly(mail, folder)
    if window_hours:
        since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).strftime("%d-%b-%Y")
        status, data = mail.uid("SEARCH", None, f"(SINCE {since})")
    else:
        status, data = mail.uid("SEARCH", None, "ALL")
    if status != "OK":
        raise RuntimeError(f"UID SEARCH failed for folder '{folder}'.")
    uid_list = sorted(int(uid) for uid in (data[0].split() if data and data[0] else []))
    if last_n:
        uid_list = uid_list[-last_n:]
    return uid_list


def fetch_message(mail, uid):
    status, msg_data = mail.uid("FETCH", str(uid), "(RFC822)")
    if status != "OK":
        raise RuntimeError(f"UID FETCH failed for UID {uid}.")
    for item in msg_data:
        if isinstance(item, tuple):
            return message_from_bytes(item[1])
    raise RuntimeError(f"No RFC822 payload returned for UID {uid}.")
