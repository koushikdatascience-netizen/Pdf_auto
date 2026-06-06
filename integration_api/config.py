"""Local installation configuration for the integration agent."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from mapping_service import (
    DEFAULT_ITEM_CODE_VERIFY_SQL,
    DEFAULT_ITEM_LOOKUP_SQL,
    DEFAULT_SUPPLIER_LOOKUP_SQL,
    MappingConfig,
)


ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)


@dataclass(frozen=True)
class Settings:
    connection_string: str
    api_key: str
    approval_secret: str
    host: str
    port: int
    max_pdf_bytes: int
    approval_ttl_seconds: int
    state_db_path: Path
    audit_log_path: Path
    upload_dir: Path
    mapping_store_path: Path
    supplier_lookup_sql: str
    item_lookup_sql: str
    item_code_verify_sql: str
    item_stock_lookup_sql: str
    transaction_type: str
    usercode: str
    sync: str
    mapping_config: MappingConfig
    enable_docs: bool
    erp_profile: str = "generic"
    erp_options: Mapping[str, Any] = field(default_factory=dict)


def _load_json(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, Mapping):
        raise RuntimeError(f"Configuration file must contain a JSON object: {path}")
    return data


def load_settings() -> Settings:
    config_path = Path(os.environ.get("ERP_AGENT_CONFIG", ROOT / "api_config.json"))
    config = _load_json(config_path)
    mappings = config.get("mappings", {})
    if not isinstance(mappings, Mapping):
        raise RuntimeError("Configuration field 'mappings' must be an object.")

    connection_string = os.environ.get(
        "SQLSERVER_CONNECTION_STRING",
        str(config.get("connection_string", "")).strip(),
    )
    api_key = os.environ.get("ERP_AGENT_API_KEY", str(config.get("api_key", "")).strip())
    approval_secret = os.environ.get(
        "ERP_AGENT_APPROVAL_SECRET",
        str(config.get("approval_secret", "")).strip(),
    )
    configured_data_dir = Path(config.get("data_dir", "agent_data"))
    data_dir = (
        configured_data_dir
        if configured_data_dir.is_absolute()
        else ROOT / configured_data_dir
    ).resolve()
    return Settings(
        connection_string=connection_string,
        api_key=api_key,
        approval_secret=approval_secret,
        host=str(config.get("host", "127.0.0.1")),
        port=int(config.get("port", 47831)),
        max_pdf_bytes=int(config.get("max_pdf_bytes", 15 * 1024 * 1024)),
        approval_ttl_seconds=int(config.get("approval_ttl_seconds", 900)),
        state_db_path=data_dir / "state",
        audit_log_path=data_dir / "audit.log",
        upload_dir=data_dir / "uploads",
        mapping_store_path=data_dir / "mappings.json",
        supplier_lookup_sql=os.environ.get(
            "ERP_SUPPLIER_LOOKUP_SQL",
            str(config.get("supplier_lookup_sql", DEFAULT_SUPPLIER_LOOKUP_SQL)),
        ),
        item_lookup_sql=os.environ.get(
            "ERP_ITEM_LOOKUP_SQL",
            str(config.get("item_lookup_sql", DEFAULT_ITEM_LOOKUP_SQL)),
        ),
        item_code_verify_sql=os.environ.get(
            "ERP_ITEM_CODE_VERIFY_SQL",
            str(config.get("item_code_verify_sql", DEFAULT_ITEM_CODE_VERIFY_SQL)),
        ),
        item_stock_lookup_sql=os.environ.get(
            "ERP_ITEM_STOCK_LOOKUP_SQL",
            str(config.get("item_stock_lookup_sql", "")).strip(),
        ),
        transaction_type=os.environ.get(
            "ERP_PURCHASE_TRANSACTION_TYPE",
            str(config.get("transaction_type", "Purchase_Add")).strip(),
        ),
        usercode=os.environ.get(
            "ERP_PURCHASE_USERCODE",
            str(config.get("usercode", "A00001")).strip(),
        ),
        sync=os.environ.get(
            "ERP_PURCHASE_SYNC",
            str(config.get("sync", "N")).strip(),
        ),
        mapping_config=MappingConfig(
            supplier_aliases=dict(mappings.get("supplier_aliases", {})),
            item_mappings=dict(mappings.get("item_mappings", {})),
        ),
        enable_docs=bool(config.get("enable_docs", False)),
        erp_profile=str(config.get("erp_profile", "generic")).strip().lower(),
        erp_options=dict(config.get("erp_options", {})),
    )


def validate_runtime_settings(settings: Settings) -> None:
    if not settings.connection_string:
        raise RuntimeError("SQLSERVER_CONNECTION_STRING or config.connection_string is required.")
    if not settings.api_key:
        raise RuntimeError("ERP_AGENT_API_KEY or config.api_key is required.")
    if not settings.approval_secret:
        raise RuntimeError("ERP_AGENT_APPROVAL_SECRET or config.approval_secret is required.")
    if settings.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("This local agent must bind to localhost unless explicitly redesigned.")
    if not settings.transaction_type:
        raise RuntimeError("transaction_type is required.")
    if len(settings.transaction_type) > 30:
        raise RuntimeError("transaction_type exceeds the ERP maximum length of 30.")
    if not settings.usercode:
        raise RuntimeError("usercode is required.")
    if len(settings.usercode) > 6:
        raise RuntimeError("usercode exceeds the ERP maximum length of 6.")
    if len(settings.sync) != 1:
        raise RuntimeError("sync must be exactly one character.")
    if settings.erp_profile not in {"generic", "mandai"}:
        raise RuntimeError("erp_profile must be 'generic' or 'mandai'.")
    if settings.erp_profile == "mandai":
        required_options = (
            "ptype",
            "purchaseacccode",
            "shopcode",
            "checked_by",
            "bill_type",
            "purchase_tax_account",
            "rounding_account",
            "supplier_name_override",
        )
        missing = [
            name for name in required_options
            if not str(settings.erp_options.get(name, "")).strip()
        ]
        if missing:
            raise RuntimeError(
                "Mandai profile is missing required erp_options: "
                + ", ".join(missing)
            )
