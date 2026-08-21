import json
from pathlib import Path

STATE_VERSION = 2
DEFAULT_STATE_PATH = Path("runtime/webde_state.json")


def _empty_state():
    return {
        "version": STATE_VERSION,
        "folders": {},
        "last_run_at_utc": None,
        "last_run_status": None,
    }


def _migrate_v1(loaded):
    """v1 tracked exactly one folder as top-level fields. Migrate into the
    v2 per-folder schema instead of discarding already-made progress."""
    return {
        "version": STATE_VERSION,
        "folders": {
            loaded["folder"]: {
                "uidvalidity": loaded.get("uidvalidity"),
                "last_processed_uid": loaded.get("last_processed_uid"),
            }
        },
        "last_run_at_utc": loaded.get("last_run_at_utc"),
        "last_run_status": loaded.get("last_run_status"),
    }


def load_state(path=DEFAULT_STATE_PATH):
    path = Path(path)
    if not path.exists():
        return _empty_state()

    with open(path, "r", encoding="utf-8") as state_file:
        loaded = json.load(state_file)

    if "folders" not in loaded and "folder" in loaded:
        loaded = _migrate_v1(loaded)

    loaded.setdefault("folders", {})
    loaded.setdefault("last_run_at_utc", None)
    loaded.setdefault("last_run_status", None)
    return loaded


def save_state(state, path=DEFAULT_STATE_PATH):
    """Atomic write (tmp file + replace) so a crash mid-write never corrupts state."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2, sort_keys=True)
        state_file.write("\n")
    tmp_path.replace(path)


def get_folder_state(state, folder):
    return state["folders"].get(folder, {"uidvalidity": None, "last_processed_uid": None})


def set_folder_state(state, folder, uidvalidity, last_processed_uid):
    state["folders"][folder] = {"uidvalidity": uidvalidity, "last_processed_uid": last_processed_uid}


def reconcile_folder_uidvalidity(folder_state, current_uidvalidity, highest_uid_fn):
    """Bootstraps (or re-bootstraps on a UIDVALIDITY change) a single folder's state
    without ever mass-resending.

    highest_uid_fn is only called when a (re-)bootstrap is actually needed, so a
    normal run with matching UIDVALIDITY does not issue an extra IMAP round-trip.
    Returns (new_folder_state, was_reset).
    """
    stored_uidvalidity = folder_state.get("uidvalidity")
    if stored_uidvalidity == current_uidvalidity and folder_state.get("last_processed_uid") is not None:
        return folder_state, False

    return {"uidvalidity": current_uidvalidity, "last_processed_uid": highest_uid_fn()}, True
