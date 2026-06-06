"""Thread-safe persistent supplier and item mappings managed by the EXE."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict

from mapping_service import MappingConfig


class MappingStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _read(self) -> Dict[str, Dict[str, str]]:
        if not self.path.exists():
            return {"supplier_aliases": {}, "item_mappings": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            "supplier_aliases": dict(data.get("supplier_aliases", {})),
            "item_mappings": dict(data.get("item_mappings", {})),
        }

    def _write(self, data: Dict[str, Dict[str, str]]) -> None:
        temporary = self.path.with_suffix(".tmp")
        content = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            self.path.write_text(content, encoding="utf-8")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def snapshot(self, base: MappingConfig) -> MappingConfig:
        with self._lock:
            stored = self._read()
        return MappingConfig(
            supplier_aliases={**base.supplier_aliases, **stored["supplier_aliases"]},
            item_mappings={**base.item_mappings, **stored["item_mappings"]},
        )

    def list(self) -> Dict[str, Dict[str, str]]:
        with self._lock:
            return self._read()

    def set_supplier(self, source_name: str, target_name: str) -> None:
        with self._lock:
            data = self._read()
            data["supplier_aliases"][source_name] = target_name
            self._write(data)

    def delete_supplier(self, source_name: str) -> bool:
        with self._lock:
            data = self._read()
            existed = data["supplier_aliases"].pop(source_name, None) is not None
            if existed:
                self._write(data)
            return existed

    def set_item(self, mapping_key: str, item_code: str) -> None:
        with self._lock:
            data = self._read()
            data["item_mappings"][mapping_key] = item_code
            self._write(data)

    def delete_item(self, mapping_key: str) -> bool:
        with self._lock:
            data = self._read()
            existed = data["item_mappings"].pop(mapping_key, None) is not None
            if existed:
                self._write(data)
            return existed
