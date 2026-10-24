"""Tool-scoped Google Flow storage helpers.

This module deliberately contains no token or cookie logic.  Its only job is
to give each local tool an isolated, app-owned storage root and to perform a
one-time, non-destructive migration of that tool's old private root.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
import threading
from pathlib import Path


STORE_SCHEMA_VERSION = 1


def get_auth_root(module_dir: Path, tool_id: str) -> Path:
    """Return the stable auth root for one tool only."""
    safe_tool_id = "".join(ch for ch in str(tool_id or "").lower() if ch.isalnum() or ch in "_-" )
    if not safe_tool_id:
        raise ValueError("tool_id is required")
    return Path(module_dir).resolve() / "data" / f"google_flow_{safe_tool_id}"


def ensure_auth_store(root: Path, tool_id: str) -> Path:
    """Create the isolated directory layout and a non-secret manifest."""
    root = Path(root).resolve()
    for name in ("profiles", "cookies", "locks"):
        (root / name).mkdir(parents=True, exist_ok=True)
    manifest = root / "store_manifest.json"
    if not manifest.exists():
        _atomic_write_json(
            manifest,
            {
                "schema_version": STORE_SCHEMA_VERSION,
                "tool_id": str(tool_id),
                "created_at": time.time(),
            },
        )
    return root


def _atomic_write_json(path: Path, payload) -> bool:
    try:
        from google_flow_auth import atomic_json
        atomic_json(path, payload)
        return True
    except Exception:
        return False


_migration_lock = threading.Lock()


def migrate_legacy_store(source: Path, target: Path, tool_id: str) -> bool:
    from google_flow_auth import ProfileLock
    target = ensure_auth_store(target, tool_id)
    with ProfileLock(_migration_lock, target / 'locks' / 'migration.lock'):
        return _migrate_legacy_store_locked(source, target, tool_id)


def _migrate_legacy_store_locked(source: Path, target: Path, tool_id: str) -> bool:
    """Copy one tool-owned legacy store without overwriting existing data.

    The source is never deleted.  Shared/foreign roots must not be passed to
    this function by callers; migration ownership is intentionally explicit.
    """
    target = ensure_auth_store(target, tool_id)
    marker = target / "legacy_migration.json"
    previous_partial = False
    if marker.exists():
        try:
            marker_data = json.loads(marker.read_text(encoding="utf-8"))
            if marker_data.get("status", "complete") == "complete":
                return False
            previous_partial = marker_data.get('schema_version', 1) < 2
        except Exception:
            # Never treat an unreadable marker as permission to overwrite.
            return False

    source = Path(source).resolve()
    copy_errors = 0
    conflicts = []
    if source != target and source.exists():
        for folder_name in ("profiles", "cookies"):
            source_folder = source / folder_name
            target_folder = target / folder_name
            if not source_folder.exists():
                continue
            for item in source_folder.iterdir():
                destination = target_folder / item.name
                if destination.exists():
                    if previous_partial:
                        # Old copytree wrote directly to destination. Its mere
                        # existence cannot prove a prior partial copy finished.
                        conflicts.append(f'{folder_name}/{item.name}')
                        copy_errors += 1
                    continue
                stage = target / '.migration-staging' / uuid.uuid4().hex / item.name
                try:
                    stage.parent.mkdir(parents=True, exist_ok=True)
                    if item.is_dir():
                        critical = [item/'Local State', item/'Default'/'Network'/'Cookies', item/'Default'/'Cookies']
                        before = {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in critical if p.is_file()}
                        shutil.copytree(item, stage)
                        after = {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in critical if p.is_file()}
                        if before != after:
                            raise OSError('source_changed_during_migration')
                    elif item.is_file():
                        shutil.copy2(item, stage)
                    stage.rename(destination)
                    stage.parent.rmdir()
                except Exception:
                    # Keep migration resumable; a locked profile can be
                    # copied by an explicit repair after both tools stop.
                    copy_errors += 1
                    continue

        registry = source / "Flow_nhieu_worker_accounts.json"
        target_registry = target / registry.name
        if registry.is_file() and not target_registry.exists():
            try:
                payload = json.loads(registry.read_text(encoding='utf-8'))
                rows = payload if isinstance(payload, list) else next((payload[k] for k in ('accounts', 'items', 'data') if isinstance(payload.get(k), list)), [payload])
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    for field, folder in (('profile_dir', 'profiles'), ('cookies_file', 'cookies')):
                        if row.get(field):
                            local = target / folder / Path(row[field]).name
                            if local.exists():
                                row[field] = str(local)
                            else:
                                row.pop(field, None)
                                row['status'] = 'migration_pending'
                if not _atomic_write_json(target_registry, payload):
                    raise OSError('registry_write_failed')
            except Exception:
                copy_errors += 1

    written = _atomic_write_json(
        marker,
        {
            "schema_version": 1 if previous_partial and conflicts else 2,
            "tool_id": str(tool_id),
            "source": str(source),
            "status": "partial" if copy_errors else "complete",
            "completed_at": time.time(),
            "conflicts": conflicts,
        },
    )
    return written and copy_errors == 0
