"""storage/ 디렉토리 준비, 레거시 logs/·data/ 마이그레이션, 업로드 정리"""

import os
import shutil
import time

from .config import (
    _TELECLAW_DIR, TELECLAW_DIR, LOGS_DIR, LOCKS_DIR, STATE_DIR, UPLOADS_DIR,
    UPLOAD_RETENTION_DAYS,
)

# 레거시 logs/ 안에 있던 로그가 아닌 파일들의 새 위치
_LEGACY_LOGS_MAP = {
    "session_ids.json": STATE_DIR,
    "teleclaw_status.json": STATE_DIR,
    "teleclaw.lock": LOCKS_DIR,
    "wrapper.lock": LOCKS_DIR,
    "images": UPLOADS_DIR,
    "files": UPLOADS_DIR,
}


def _migrate_dir(src_dir: str, dest_for) -> list[str]:
    """src_dir의 항목을 dest_for(name)으로 이동. 대상이 이미 있으면 건너뜀. 비면 src_dir 삭제."""
    notes = []
    for name in os.listdir(src_dir):
        dest = os.path.join(dest_for(name), name)
        if os.path.exists(dest):
            continue
        try:
            shutil.move(os.path.join(src_dir, name), dest)
            notes.append(f"{name} → {dest}")
        except OSError as e:  # 다른 프로세스가 열고 있는 파일 (예: 구버전 task_out.log)
            notes.append(f"{name} 이동 실패: {e}")
    try:
        os.rmdir(src_dir)
    except OSError:
        pass
    return notes


def ensure_storage() -> list[str]:
    """storage 하위 디렉토리 생성 + 레거시 디렉토리 마이그레이션. 처리 내역 반환."""
    for d in (LOGS_DIR, LOCKS_DIR, STATE_DIR, UPLOADS_DIR):
        os.makedirs(d, exist_ok=True)
    notes = []
    # 예전에는 config.py와 daemon이 서로 다른 기준 디렉토리를 쓸 수 있었으므로 둘 다 확인
    for base in {TELECLAW_DIR, _TELECLAW_DIR}:
        legacy_logs = os.path.join(base, "logs")
        if os.path.isdir(legacy_logs) and legacy_logs != LOGS_DIR:
            notes += _migrate_dir(legacy_logs, lambda n: _LEGACY_LOGS_MAP.get(n, LOGS_DIR))
        legacy_data = os.path.join(base, "data")
        if os.path.isdir(legacy_data) and legacy_data != STATE_DIR:
            notes += _migrate_dir(legacy_data, lambda n: STATE_DIR)
    return notes


def prune_uploads() -> int:
    """UPLOAD_RETENTION_DAYS보다 오래된 업로드 파일 삭제. 삭제 개수 반환."""
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
