import json
from pathlib import Path

STATE_VERSION = 1
DEFAULT_STATE_PATH = Path("runtime/webde_state.json")


def _empty_state(folder):
    return {
        "version": STATE_VERSION,
        "folder": folder,
        "uidvalidity": None,
        "last_processed_uid": None,
        "last_run_at_utc": None,
        "last_run_status": None,
    }


def load_state(path=DEFAULT_STATE_PATH, folder="INBOX"):
    path = Path(path)
    if not path.exists():
        return _empty_state(folder)
    with open(path, "r", encoding="utf-8") as state_file:
        return json.load(state_file)


def save_state(state, path=DEFAULT_STATE_PATH):
    """Atomic write (tmp file + replace) so a crash mid-write never corrupts state."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2, sort_keys=True)
        state_file.write("\n")
    tmp_path.replace(path)


def reconcile_uidvalidity(state, folder, current_uidvalidity, highest_uid_fn):
    """Bootstraps (or re-bootstraps on a UIDVALIDITY change) without ever mass-resending.

    highest_uid_fn is only called when a (re-)bootstrap is actually needed, so a normal
    run with matching UIDVALIDITY does not issue an extra IMAP round-trip.
    Returns (new_state, was_reset).
    """
    stored_uidvalidity = state.get("uidvalidity")
    if stored_uidvalidity == current_uidvalidity and state.get("last_processed_uid") is not None:
        return state, False

    new_state = dict(state)
    new_state["folder"] = folder
    new_state["uidvalidity"] = current_uidvalidity
    new_state["last_processed_uid"] = highest_uid_fn()
    return new_state, True
