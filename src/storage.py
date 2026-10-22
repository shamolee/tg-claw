"""Prepare storage/, migrate legacy logs/ and data/, prune old uploads"""

import glob
import os
import shutil
import time

from .config import (
    _TG_CLAW_DIR, TG_CLAW_DIR, LOGS_DIR, LOCKS_DIR, STATE_DIR, UPLOADS_DIR,
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

# Files named before the TeleClaw → tg-claw rename: (dir, old name, new name)
_LEGACY_NAMES = [
    (LOGS_DIR, "teleclaw.log", "tg_claw.log"),
    (LOGS_DIR, "teleclaw_stderr.log", "tg_claw_stderr.log"),
    (LOCKS_DIR, "teleclaw.lock", "tg_claw.lock"),
    (STATE_DIR, "teleclaw_status.json", "tg_claw_status.json"),
    (STATE_DIR, "teleClaw.db", "tg_claw.db"),
    (STATE_DIR, "teleClaw.db-wal", "tg_claw.db-wal"),
    (STATE_DIR, "teleClaw.db-shm", "tg_claw.db-shm"),
]


def _rename_legacy_names() -> list[str]:
    """Rename pre-rename files inside storage/, skipping any whose new name already exists."""
    pairs = [(os.path.join(d, old), os.path.join(d, new)) for d, old, new in _LEGACY_NAMES]
    for path in glob.glob(os.path.join(LOGS_DIR, "teleclaw_????-??-??.log")):  # daily log archives
        pairs.append((path, os.path.join(LOGS_DIR, "tg_claw_" + os.path.basename(path)[len("teleclaw_"):])))
    notes = []
    for old, new in pairs:
        if os.path.exists(old) and not os.path.exists(new):
            try:
                os.replace(old, new)
                notes.append(f"{os.path.basename(old)} → {os.path.basename(new)}")
            except OSError as e:
                notes.append(f"failed to rename {os.path.basename(old)}: {e}")
    return notes


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
    """Create the storage subdirectories, migrate legacy dirs and file names. Returns a list of what was done."""
    for d in (LOGS_DIR, LOCKS_DIR, STATE_DIR, UPLOADS_DIR):
        os.makedirs(d, exist_ok=True)
    notes = []
    # config.py and the daemon used to resolve different base dirs, so check both
    for base in {TG_CLAW_DIR, _TG_CLAW_DIR}:
        legacy_logs = os.path.join(base, "logs")
        if os.path.isdir(legacy_logs) and legacy_logs != LOGS_DIR:
            notes += _migrate_dir(legacy_logs, lambda n: _LEGACY_LOGS_MAP.get(n, LOGS_DIR))
        legacy_data = os.path.join(base, "data")
        if os.path.isdir(legacy_data) and legacy_data != STATE_DIR:
            notes += _migrate_dir(legacy_data, lambda n: STATE_DIR)
    return notes + _rename_legacy_names()


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
