"""Prepare storage/, migrate legacy logs/ and data/, prune old uploads"""

import os
import shutil
import time

from .config import (
    _TELECLAW_DIR, TELECLAW_DIR, LOGS_DIR, LOCKS_DIR, STATE_DIR, UPLOADS_DIR,
    UPLOAD_RETENTION_DAYS,
)

# New locations for the non-log files that used to live in legacy logs/
_LEGACY_LOGS_MAP = {
    "session_ids.json": STATE_DIR,
    "teleclaw_status.json": STATE_DIR,
    "teleclaw.lock": LOCKS_DIR,
    "wrapper.lock": LOCKS_DIR,
    "images": UPLOADS_DIR,
    "files": UPLOADS_DIR,
}


def _migrate_dir(src_dir: str, dest_for) -> list[str]:
    """Move entries of src_dir to dest_for(name), skipping existing targets. Removes src_dir if empty."""
    notes = []
    for name in os.listdir(src_dir):
        dest = os.path.join(dest_for(name), name)
        if os.path.exists(dest):
            continue
        try:
            shutil.move(os.path.join(src_dir, name), dest)
            notes.append(f"{name} → {dest}")
        except OSError as e:  # file held open by another process (e.g. an old task_out.log)
            notes.append(f"failed to move {name}: {e}")
    try:
        os.rmdir(src_dir)
    except OSError:
        pass
    return notes


def ensure_storage() -> list[str]:
    """Create the storage subdirectories and migrate legacy dirs. Returns a list of what was done."""
    for d in (LOGS_DIR, LOCKS_DIR, STATE_DIR, UPLOADS_DIR):
        os.makedirs(d, exist_ok=True)
    notes = []
    # config.py and the daemon used to resolve different base dirs, so check both
    for base in {TELECLAW_DIR, _TELECLAW_DIR}:
        legacy_logs = os.path.join(base, "logs")
        if os.path.isdir(legacy_logs) and legacy_logs != LOGS_DIR:
            notes += _migrate_dir(legacy_logs, lambda n: _LEGACY_LOGS_MAP.get(n, LOGS_DIR))
        legacy_data = os.path.join(base, "data")
        if os.path.isdir(legacy_data) and legacy_data != STATE_DIR:
            notes += _migrate_dir(legacy_data, lambda n: STATE_DIR)
    return notes


def prune_uploads() -> int:
    """Delete uploads older than UPLOAD_RETENTION_DAYS. Returns the number removed."""
    if UPLOAD_RETENTION_DAYS <= 0 or not os.path.isdir(UPLOADS_DIR):
        return 0
    cutoff = time.time() - UPLOAD_RETENTION_DAYS * 86400
    removed = 0
    for dirpath, _, filenames in os.walk(UPLOADS_DIR):
        for fname in filenames:
            path = os.path.join(dirpath, fname)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    return removed
