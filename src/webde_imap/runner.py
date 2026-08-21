import logging
from datetime import datetime, timezone

from . import classify as classify_mod
from . import event_id as event_id_mod
from . import forward
from . import imap_client
from . import mail_content
from . import state as state_mod

logger = logging.getLogger("webde_imap")


class FolderResult:
    def __init__(self, folder):
        self.folder = folder
        self.processed_uids = []
        self.forwarded_count = 0
        self.ignored_count = 0
        self.had_failure = False
        self.failed_uid = None

    def __repr__(self):
        return (
            f"FolderResult(folder={self.folder!r}, processed={len(self.processed_uids)}, "
            f"forwarded={self.forwarded_count}, ignored={self.ignored_count}, had_failure={self.had_failure})"
        )


class RunResult:
    def __init__(self):
        self.folder_results = []

    def folder(self, name):
        for folder_result in self.folder_results:
            if folder_result.folder == name:
                return folder_result
        return None

    @property
    def had_failure(self):
        return any(fr.had_failure for fr in self.folder_results)

    @property
    def forwarded_count(self):
        return sum(fr.forwarded_count for fr in self.folder_results)

    @property
    def ignored_count(self):
        return sum(fr.ignored_count for fr in self.folder_results)

    @property
    def total_processed(self):
        return sum(len(fr.processed_uids) for fr in self.folder_results)

    def __repr__(self):
        return f"RunResult(folders={[fr.folder for fr in self.folder_results]}, had_failure={self.had_failure})"


def _process_folder(mail, folder, config, rules, state, state_path, dry_run, bootstrap_last_n, bootstrap_window_hours):
    result = FolderResult(folder)
    folder_state = state_mod.get_folder_state(state, folder)

    uidvalidity = imap_client.get_uidvalidity(mail, folder)
    bootstrap_requested = bool(bootstrap_last_n or bootstrap_window_hours)

    if bootstrap_requested:
        uid_list = imap_client.list_uids_for_bootstrap(
            mail, folder, last_n=bootstrap_last_n, window_hours=bootstrap_window_hours
        )
        last_processed_uid = folder_state.get("last_processed_uid")
        if last_processed_uid is None:
            last_processed_uid = 0
        state_mod.set_folder_state(state, folder, uidvalidity, last_processed_uid)
    else:
        new_folder_state, was_reset = state_mod.reconcile_folder_uidvalidity(
            folder_state, uidvalidity, highest_uid_fn=lambda: imap_client.get_highest_uid(mail, folder)
        )
        state_mod.set_folder_state(state, folder, new_folder_state["uidvalidity"], new_folder_state["last_processed_uid"])
        if was_reset:
            logger.warning("UIDVALIDITY changed for folder '%s' -- re-bootstrapping without resend.", folder)
            if not dry_run:
                state_mod.save_state(state, state_path)
            uid_list = []
        else:
            uid_list = imap_client.list_uids_after(mail, folder, new_folder_state["last_processed_uid"])

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
            logger.info("folder=%s uid=%s category=%s event_id=%s", folder, uid, category, eid)

            if not dry_run:
                state_mod.set_folder_state(state, folder, uidvalidity, uid)
                state["last_run_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                state_mod.save_state(state, state_path)

        except Exception as exc:  # noqa: BLE001 -- one bad UID must not crash the whole run silently
            result.had_failure = True
            result.failed_uid = uid
            logger.error(
                "Processing failed for folder=%s uid=%s (%s) -- stopping this folder.",
                folder, uid, type(exc).__name__,
            )
            break

    return result


def run(mail, config, rules, state_path, dry_run=False, bootstrap_last_n=None, bootstrap_window_hours=None):
    """Processes every configured folder independently and sequentially.

    A failure in one folder stops only that folder (its own UID progress up to
    the failure is still persisted) -- it does not block the remaining folders.
    The overall run is reported as failed if any folder had a failure.
    """
    state = state_mod.load_state(state_path)
    result = RunResult()

    for folder in config["folders"]:
        folder_result = _process_folder(
            mail, folder, config, rules, state, state_path, dry_run, bootstrap_last_n, bootstrap_window_hours
        )
        result.folder_results.append(folder_result)

    if not dry_run:
        state["last_run_status"] = "PARTIAL" if result.had_failure else "OK"
        state["last_run_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        state_mod.save_state(state, state_path)

    return result
