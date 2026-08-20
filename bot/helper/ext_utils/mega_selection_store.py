# mega_selection_store.py
# Lives at:  web/mega_selection_store.py   (imported by wserver)
#        AND bot/helper/ext_utils/mega_selection_store.py  (imported by mega_download)
#
# Both copies must be identical — or use a symlink:
#   ln -s ../../web/mega_selection_store.py \
#         bot/helper/ext_utils/mega_selection_store.py
#
# The store persists MEGA file-selection sessions to disk so the two
# processes (bot + wserver) can share state without a database.

import json
import os
import time
from typing import Optional

_BASE_DIR = "/usr/src/app/downloads/.mega_selections"

_STALE_AFTER_SECONDS = 6 * 60 * 60  # 6 hours


def _path(gid: str) -> str:
    return os.path.join(_BASE_DIR, f"{gid}.json")


def _is_safe_gid(gid: str) -> bool:
    if not gid or not isinstance(gid, str):
        return False
    return all(c.isalnum() or c in "-_" for c in gid)


def write_state(gid: str, file_list_metadata: list, selected_ids) -> bool:
    """
    Atomically write the selection state for *gid*.

    :param gid:                GID string (alphanumeric + - _).
    :param file_list_metadata: Flat list of file-dicts from _walk_mega_node.
    :param selected_ids:       Iterable of file_id strings that are selected.
    :returns: True on success, False on error.
    """
    if not _is_safe_gid(gid):
        return False
    try:
        os.makedirs(_BASE_DIR, exist_ok=True)
        target = _path(gid)
        tmp = f"{target}.{os.getpid()}.{int(time.time()*1000)}.tmp"
        payload = {
            "file_list": file_list_metadata,
            "selected_ids": list(selected_ids),
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, target)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except (NameError, OSError):
            pass
        return False


def read_state(gid: str) -> Optional[dict]:
    """Return the full state dict or None if not found / corrupt."""
    if not _is_safe_gid(gid):
        return None
    try:
        with open(_path(gid), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def update_selected_ids(gid: str, selected_ids) -> bool:
    """
    Overwrite only the selected_ids portion of an existing state.
    Returns False if the state file doesn't exist yet.
    """
    data = read_state(gid)
    if data is None:
        return False
    file_list = data.get("file_list", [])
    if not isinstance(file_list, list):
        file_list = []
    return write_state(gid, file_list, list(selected_ids))


def delete_state(gid: str) -> None:
    """Remove the state file for *gid* (no-op if absent)."""
    if not _is_safe_gid(gid):
        return
    try:
        os.remove(_path(gid))
    except (FileNotFoundError, OSError):
        pass


def get_file_list(gid: str) -> Optional[list]:
    """Return just the file_list portion, or None."""
    data = read_state(gid)
    if data is None:
        return None
    file_list = data.get("file_list")
    if not isinstance(file_list, list):
        return None
    return file_list


def get_selected_ids(gid: str) -> list:
    """Return the list of selected file_id strings (empty list if absent)."""
    data = read_state(gid)
    if data is None:
        return []
    selected = data.get("selected_ids", [])
    if not isinstance(selected, list):
        return []
    return selected


def cleanup_stale_states(max_age_seconds: int = _STALE_AFTER_SECONDS) -> int:
    """
    Delete state files older than *max_age_seconds*.
    Returns the number of files removed.
    Call periodically from a bot startup hook or a cron task.
    """
    if not os.path.isdir(_BASE_DIR):
        return 0
    deadline = time.time() - max_age_seconds
    removed = 0
    try:
        for entry in os.scandir(_BASE_DIR):
            if not entry.is_file():
                continue
            name = entry.name
            if not (name.endswith(".json") or ".tmp" in name):
                continue
            try:
                if entry.stat().st_mtime < deadline:
                    os.remove(entry.path)
                    removed += 1
            except OSError:
                continue
    except OSError:
        return removed
    return removed
