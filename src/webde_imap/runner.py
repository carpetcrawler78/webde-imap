import logging
from datetime import datetime, timezone

from . import classify as classify_mod
from . import event_id as event_id_mod
from . import forward
from . import imap_client
from . import mail_content
from . import state as state_mod

logger = logging.getLogger("webde_imap")


class RunResult:
    def __init__(self):
        self.processed_uids = []
        self.forwarded_count = 0
        self.ignored_count = 0
        self.had_failure = False
        self.failed_uid = None

    def __repr__(self):
        return (
            f"RunResult(processed={len(self.processed_uids)}, forwarded={self.forwarded_count}, "
            f"ignored={self.ignored_count}, had_failure={self.had_failure})"
        )


def run(mail, config, rules, state_path, dry_run=False, bootstrap_last_n=None, bootstrap_window_hours=None):
    folder = config["inbox_folder"]
    result = RunResult()

    current_state = state_mod.load_state(state_path, folder=folder)
    uidvalidity = imap_client.get_uidvalidity(mail, folder)
    bootstrap_requested = bool(bootstrap_last_n or bootstrap_window_hours)

    if bootstrap_requested:
        uid_list = imap_client.list_uids_for_bootstrap(
            mail, folder, last_n=bootstrap_last_n, window_hours=bootstrap_window_hours
        )
        current_state = dict(current_state)
        current_state["folder"] = folder
        current_state["uidvalidity"] = uidvalidity
        if current_state.get("last_processed_uid") is None:
            current_state["last_processed_uid"] = 0
    else:
        current_state, was_reset = state_mod.reconcile_uidvalidity(
            current_state,
            folder,
            uidvalidity,
            highest_uid_fn=lambda: imap_client.get_highest_uid(mail, folder),
        )
        if was_reset:
            logger.warning("UIDVALIDITY changed for folder '%s' -- re-bootstrapping without resend.", folder)
            if not dry_run:
                state_mod.save_state(current_state, state_path)
            uid_list = []
        else:
            uid_list = imap_client.list_uids_after(mail, folder, current_state["last_processed_uid"])

    for uid in uid_list:
        try:
            message = imap_client.fetch_message(mail, uid)
            content = mail_content.extract_message_content(message)
            category = classify_mod.classify_message(message, content, rules)
            eid = event_id_mod.compute_event_id(
                config["imap_username"], folder, uidvalidity, uid, message.get("Message-Id")
            )

            if category != "IGNORE":
                if not dry_run:
                    forward_msg = forward.build_forward_message(message, content, category, eid, folder, uid, config)
                    forward.send_via_smtp(forward_msg, config)
                result.forwarded_count += 1
            else:
                result.ignored_count += 1

            result.processed_uids.append(uid)
            logger.info("uid=%s category=%s event_id=%s", uid, category, eid)

            if not dry_run:
                current_state["last_processed_uid"] = uid
                current_state["last_run_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                state_mod.save_state(current_state, state_path)

        except Exception as exc:  # noqa: BLE001 -- must not let one bad UID crash the whole run silently
            result.had_failure = True
            result.failed_uid = uid
            logger.error("Processing failed for uid=%s (%s) -- stopping run.", uid, type(exc).__name__)
            break

    if not dry_run:
        current_state["last_run_status"] = "PARTIAL" if result.had_failure else "OK"
        current_state["last_run_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        state_mod.save_state(current_state, state_path)

    return result
