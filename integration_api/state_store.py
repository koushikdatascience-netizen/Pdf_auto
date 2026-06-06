"""Atomic file-backed preview and idempotency state for the local agent."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional


class StateStore:
    """Persist preview state as one atomic JSON file per preview.

    The local agent runs as one process. A process lock protects transitions,
    while atomic file replacement protects against partial writes on shutdown.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _record_path(self, preview_id: str) -> Path:
        if not preview_id.isalnum():
            raise ValueError("Invalid preview id.")
        return self.path / f"{preview_id}.json"

    def _resolution_path(self, resolution_id: str) -> Path:
        if not resolution_id.isalnum():
            raise ValueError("Invalid resolution id.")
        return self.path / f"resolution-{resolution_id}.json"

    def _write(self, record: Dict[str, Any]) -> None:
        target = self._record_path(record["preview_id"])
        temporary = target.with_suffix(".tmp")
        content = json.dumps(record, ensure_ascii=False, indent=2)
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
        except OSError:
            # Some managed/network-style Windows drives block atomic rename.
            target.write_text(content, encoding="utf-8")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def create(
        self,
        preview_id: str,
        expires_at: int,
        digest: str,
        payload: Dict[str, Any],
        preview: Dict[str, Any],
    ) -> None:
        with self._lock:
            self._write(
                {
                    "preview_id": preview_id,
                    "created_at": int(time.time()),
                    "expires_at": expires_at,
                    "status": "approved",
                    "digest": digest,
                    "payload_json": payload,
                    "preview_json": preview,
                    "result_json": None,
                    "error": None,
                }
            )

    def get(self, preview_id: str) -> Optional[Dict[str, Any]]:
        path = self._record_path(preview_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def claim_for_insert(self, preview_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self.get(preview_id)
            if state is None:
                raise KeyError("Preview not found.")
            if state["status"] == "inserted":
                return state
            if state["status"] == "inserting":
                raise RuntimeError("Insert is already in progress.")
            if int(state["expires_at"]) < int(time.time()):
                raise RuntimeError("Preview has expired.")
            state["status"] = "inserting"
            state["error"] = None
            self._write(state)
            return state

    def mark_inserted(self, preview_id: str, result: Dict[str, Any]) -> None:
        with self._lock:
            state = self.get(preview_id)
            if state is None:
                raise KeyError("Preview not found.")
            state["status"] = "inserted"
            state["result_json"] = result
            self._write(state)

    def mark_failed(self, preview_id: str, error: str) -> None:
        with self._lock:
            state = self.get(preview_id)
            if state is None:
                return
            state["status"] = "approved"
            state["error"] = error[:2000]
            self._write(state)

    def create_resolution(
        self,
        resolution_id: str,
        expires_at: int,
        payload: Dict[str, Any],
    ) -> None:
        with self._lock:
            path = self._resolution_path(resolution_id)
            temporary = path.with_suffix(".tmp")
            content = json.dumps(
                {
                    "resolution_id": resolution_id,
                    "created_at": int(time.time()),
                    "expires_at": expires_at,
                    "payload_json": payload,
                },
                ensure_ascii=False,
                indent=2,
            )
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)

    def get_resolution(self, resolution_id: str) -> Optional[Dict[str, Any]]:
        path = self._resolution_path(resolution_id)
        if not path.exists():
            return None
        state = json.loads(path.read_text(encoding="utf-8"))
        if int(state["expires_at"]) < int(time.time()):
            path.unlink(missing_ok=True)
            return None
        return state

    def find_inserted_by_file_hash(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """Find a previously inserted preview created from the exact same PDF bytes."""
        for path in self.path.glob("*.json"):
            if path.name.startswith("resolution-"):
                continue
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                state.get("status") == "inserted"
                and state.get("payload_json", {}).get("file_hash") == file_hash
            ):
                return state
        return None
