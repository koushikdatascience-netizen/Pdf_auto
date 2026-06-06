"""FastAPI application for the local ERP integration agent."""

from __future__ import annotations

import json
import hashlib
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

import db
from extract_pdf_json import extract_pdf
from mapping_service import MappingConfig, MappingError
from pdf_purchase_adapter import PdfPurchaseAdapterError, normalize_extracted_purchases
from purchase_service import inspect_purchase_mappings, insert_purchase, preview_purchase
from purchase_service import PurchaseServiceError
from schema_check import (
    EXPECTED_INSERT_COLUMNS,
    FORBIDDEN_WRITE_TABLES,
    inspect_foreign_keys,
    inspect_table,
)
from validation import ValidationError

from .config import Settings, load_settings
from .mapping_store import MappingStore
from .models import (
    ItemMappingRequest,
    PurchaseInsertRequest,
    PurchasePreviewRequest,
    SupplierMappingRequest,
)
from .security import (
    create_approval_token,
    payload_digest,
    require_api_key,
    verify_approval_token,
)
from .state_store import StateStore


def _configure_logging(settings: Settings) -> logging.Handler:
    settings.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(settings.audit_log_path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    return handler


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    audit_handler = _configure_logging(settings)
    logger = logging.getLogger("integration_api")
    store = StateStore(settings.state_db_path)
    mapping_store = MappingStore(settings.mapping_store_path)
    auth = require_api_key(settings.api_key)

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield
        finally:
            logging.getLogger().removeHandler(audit_handler)
            audit_handler.close()

    app = FastAPI(
        title="Local ERP Purchase Integration Agent",
        version="1.5.0",
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.mapping_store = mapping_store
    app.state.audit_handler = audit_handler

    def open_connection() -> Any:
        if not settings.connection_string:
            raise HTTPException(status_code=503, detail="Database connection is not configured.")
        try:
            return db.connect(settings.connection_string)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Database connection failed: {exc}") from exc

    def current_mapping_config() -> MappingConfig:
        return mapping_store.snapshot(settings.mapping_config)

    def require_read_only_sql(sql: str) -> None:
        normalized = " ".join(sql.upper().split())
        forbidden = (" INSERT ", " UPDATE ", " DELETE ", " MERGE ", " EXEC ", " DROP ",
                     " ALTER ", " CREATE ", " TRUNCATE ")
        padded = f" {normalized} "
        if not normalized.startswith("SELECT ") or any(token in padded for token in forbidden):
            raise HTTPException(status_code=503, detail="Configured stock query must be read-only SELECT SQL.")

    def master_suggestions(connection: Any, issue: Dict[str, Any], companycode: str) -> list:
        cursor = connection.cursor()
        try:
            words = [word for word in str(issue["source"]).replace("/", " ").split() if len(word) >= 3]
            search = words[0] if words else str(issue["source"])
            if issue["type"] == "supplier":
                cursor.execute(
                    """
                    SELECT TOP 10 ledgerCode, ledgerName
                    FROM dbo.MasterAccountsLedger
                    WHERE companyCode = ? AND ledgerName LIKE ?
                    ORDER BY ledgerName
                    """,
                    companycode,
                    f"%{search}%",
                )
                return [
                    {"suppliercode": str(row[0]).strip(), "supplier_name": str(row[1]).strip()}
                    for row in cursor.fetchall()
                ]
            cursor.execute(
                """
                SELECT TOP 10 itemcode, itemname, ml, packing, strengthname
                FROM dbo.itemmst
                WHERE itemname LIKE ? OR itemcode LIKE ?
                ORDER BY itemname
                """,
                f"%{search}%",
                f"%{search}%",
            )
            return [
                {
                    "itemcode": str(row[0]).strip(),
                    "item_name": str(row[1]).strip(),
                    "ml": row[2],
                    "packing": row[3],
                    "strength_name": row[4],
                }
                for row in cursor.fetchall()
            ]
        finally:
            cursor.close()

    async def save_pdf_upload(pdf: UploadFile) -> tuple[str, Path, str]:
        filename = Path(pdf.filename or "upload.pdf").name
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=415, detail="Only PDF files are supported.")
        content = await pdf.read(settings.max_pdf_bytes + 1)
        if len(content) > settings.max_pdf_bytes:
            raise HTTPException(status_code=413, detail="PDF exceeds configured size limit.")
        if not content.startswith(b"%PDF-"):
            raise HTTPException(status_code=415, detail="Uploaded file is not a valid PDF.")
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        path = settings.upload_dir / f"{uuid.uuid4().hex}-{filename}"
        path.write_bytes(content)
        return filename, path, hashlib.sha256(content).hexdigest()

    def remove_uploaded_pdf(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Temporary PDF cleanup deferred path=%s error=%s.", path, exc)

    def create_preview_approval(
        invoice: Dict[str, Any],
        companycode: str,
        yearcode: str,
        strict_total: bool,
        preview_data: Dict[str, Any],
        file_hash: str | None = None,
    ) -> Dict[str, Any]:
        payload = {
            "companycode": companycode,
            "yearcode": yearcode,
            "invoice": invoice,
            "strict_total": strict_total,
            "file_hash": file_hash,
        }
        digest = payload_digest(payload)
        preview_id = uuid.uuid4().hex
        expires_at = int(time.time()) + settings.approval_ttl_seconds
        store.create(preview_id, expires_at, digest, payload, preview_data)
        token = create_approval_token(preview_id, digest, expires_at, settings.approval_secret)
        return {
            "ready_for_insert": True,
            "preview_id": preview_id,
            "approval_token": token,
            "expires_at": expires_at,
            "preview": preview_data,
        }

    def prepare_invoice_previews(
        invoices: list,
        connection: Any,
        *,
        companycode: str,
        yearcode: str,
        strict_total: bool,
        file_hash: str | None = None,
    ) -> Dict[str, Any]:
        preview_results = []
        resolution_issues = []
        duplicates = []
        for invoice in invoices:
            issues = inspect_purchase_mappings(
                invoice,
                connection,
                companycode=companycode,
                mapping_config=current_mapping_config(),
                supplier_lookup_sql=settings.supplier_lookup_sql,
                item_lookup_sql=settings.item_lookup_sql,
                item_code_verify_sql=settings.item_code_verify_sql,
                strict_total=strict_total,
            )
            for issue in issues:
                issue["suggestions"] = master_suggestions(connection, issue, companycode)
                issue["actions"] = {
                    "search_master": True,
                    "save_mapping": True,
                    "check_live_stock": issue["type"] == "item",
                    "create_in_erp": issue["type"] == "item",
                    "instant_create_available": False,
                }
            resolution_issues.extend(issues)
            if issues:
                continue
            preview_data = preview_purchase(
                invoice,
                connection,
                companycode=companycode,
                yearcode=yearcode,
                mapping_config=current_mapping_config(),
                supplier_lookup_sql=settings.supplier_lookup_sql,
                item_lookup_sql=settings.item_lookup_sql,
                item_code_verify_sql=settings.item_code_verify_sql,
                strict_total=strict_total,
            )
            cursor = connection.cursor()
            try:
                existing = db.find_existing_purchase(
                    cursor,
                    companycode,
                    yearcode,
                    preview_data["suppliercode"],
                    preview_data["docno"],
                )
            finally:
                cursor.close()
                connection.rollback()
            if existing:
                duplicates.append(
                    {
                        "suppliercode": preview_data["suppliercode"],
                        "docno": preview_data["docno"],
                        **existing,
                    }
                )
                continue
            preview_results.append((invoice, preview_data))
        if resolution_issues:
            resolution_id = uuid.uuid4().hex
            expires_at = int(time.time()) + settings.approval_ttl_seconds
            store.create_resolution(
                resolution_id,
                expires_at,
                {
                    "invoices": invoices,
                    "companycode": companycode,
                    "yearcode": yearcode,
                    "strict_total": strict_total,
                    "file_hash": file_hash,
                },
            )
            return {
                "status_code": 409,
                "body": {
                    "ready_for_insert": False,
                    "resolution_required": True,
                    "resolution_id": resolution_id,
                    "expires_at": expires_at,
                    "unresolved_count": len(resolution_issues),
                    "unresolved": resolution_issues,
                    "action": "Resolve each issue, then retry using the resolution_id.",
                },
            }
        if duplicates:
            return {
                "status_code": 409,
                "body": {
                    "ready_for_insert": False,
                    "duplicate": True,
                    "duplicates": duplicates,
                    "action": "Open the existing ERP purchase. No insert is allowed.",
                },
            }
        approvals = [
            create_preview_approval(
                invoice, companycode, yearcode, strict_total, preview_data, file_hash
            )
            for invoice, preview_data in preview_results
        ]
        return {"status_code": 200, "body": {"ready_for_insert": True, "purchases": approvals}}

    @app.exception_handler(ValidationError)
    @app.exception_handler(PdfPurchaseAdapterError)
    async def validation_handler(_, exc: Exception):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(MappingError)
    async def mapping_handler(_, exc: MappingError):
        return JSONResponse(
            status_code=409,
            content={
                "ready_for_insert": False,
                "detail": str(exc),
                "action": "Add or correct the supplier/item master record, then preview again.",
                "unresolved": {
                    "type": exc.mapping_type,
                    "source": exc.source,
                    "mapping_key": exc.mapping_key,
                },
            },
        )

    @app.exception_handler(db.DatabaseError)
    @app.exception_handler(PurchaseServiceError)
    async def database_handler(_, exc: Exception):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unexpected_handler(_: Request, exc: Exception):
        error_id = uuid.uuid4().hex[:12]
        logger.exception("Unexpected API error id=%s.", error_id)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Unexpected integration-agent error.",
                "error_id": error_id,
            },
        )

    @app.get("/api/v1/health")
    def health(_: None = Depends(auth)) -> Dict[str, Any]:
        database = "not_configured"
        if settings.connection_string:
            try:
                connection = open_connection()
                connection.close()
                database = "connected"
            except HTTPException:
                database = "unavailable"
        return {"status": "ok", "database": database, "version": app.version}

    @app.post("/api/v1/extract")
    async def extract(
        pdf: UploadFile = File(...),
        _: None = Depends(auth),
    ) -> Dict[str, Any]:
        filename, path, _ = await save_pdf_upload(pdf)
        try:
            extracted = extract_pdf(path)
            logger.info("Extracted PDF file=%s pages=%s.", filename, extracted["page_count"])
            return {"success": True, "extracted": extracted}
        finally:
            remove_uploaded_pdf(path)

    @app.post("/api/v1/purchases/from-pdf/preview")
    async def preview_from_pdf(
        companycode: str = Form(...),
        yearcode: str = Form(...),
        strict_total: bool = Form(True),
        pdf: UploadFile = File(...),
        _: None = Depends(auth),
    ) -> Dict[str, Any]:
        """Extract PDF, normalize purchases, validate masters, and create approvals."""
        filename, path, file_hash = await save_pdf_upload(pdf)
        connection = open_connection()
        try:
            previous = store.find_inserted_by_file_hash(file_hash)
            if previous:
                return JSONResponse(
                    status_code=409,
                    content=jsonable_encoder(
                        {
                            "ready_for_insert": False,
                            "duplicate": True,
                            "duplicate_type": "exact_pdf",
                            "existing_preview_id": previous["preview_id"],
                            "existing_result": previous.get("result_json"),
                            "action": "Open the existing ERP purchase. No insert is allowed.",
                        }
                    ),
                )
            extracted = extract_pdf(path)
            try:
                invoices = normalize_extracted_purchases(extracted)
            except PdfPurchaseAdapterError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": str(exc),
                        "extraction_method": extracted.get("extraction_method"),
                        "needs_ocr": extracted.get("needs_ocr"),
                        "items_extracted": len(extracted.get("items") or []),
                        "manufacturer_groups_extracted": len(
                            extracted.get("manufacturer_groups") or []
                        ),
                    },
                ) from exc
            prepared = prepare_invoice_previews(
                invoices,
                connection,
                companycode=companycode,
                yearcode=yearcode,
                strict_total=strict_total,
                file_hash=file_hash,
            )
            body = prepared["body"]
            body["source_file"] = filename
            if prepared["status_code"] != 200:
                return JSONResponse(status_code=prepared["status_code"], content=jsonable_encoder(body))
            approvals = body["purchases"]
            logger.info(
                "Prepared PDF purchase previews file=%s purchases=%s.",
                filename,
                len(approvals),
            )
            return {
                "ready_for_insert": True,
                "source_file": filename,
                "extraction": {
                    "page_count": extracted.get("page_count"),
                    "extraction_method": extracted.get("extraction_method"),
                    "needs_ocr": extracted.get("needs_ocr"),
                },
                "purchase_count": len(approvals),
                "purchases": approvals,
            }
        finally:
            connection.close()
            remove_uploaded_pdf(path)

    @app.get("/api/v1/schema-check")
    def schema_check(_: None = Depends(auth)) -> Dict[str, Any]:
        connection = open_connection()
        cursor = connection.cursor()
        try:
            report: Dict[str, Any] = {"compatible": True, "blocking_issues": [], "tables": {}}
            for table, inserted_columns in EXPECTED_INSERT_COLUMNS.items():
                columns = inspect_table(cursor, table)
                actual = {column["name"].lower(): column for column in columns}
                inserted = {column.lower() for column in inserted_columns}
                missing = [column for column in inserted_columns if column.lower() not in actual]
                unhandled = [
                    column["name"]
                    for column in columns
                    if not column["nullable"]
                    and column["default"] is None
                    and not column["identity"]
                    and column["name"].lower() not in inserted
                ]
                report["tables"][table] = {
                    "missing_insert_columns": missing,
                    "required_columns_not_inserted": unhandled,
                    "foreign_keys": inspect_foreign_keys(cursor, table),
                }
                if missing:
                    report["blocking_issues"].append(
                        f"{table} is missing expected columns: {', '.join(missing)}"
                    )
                if unhandled:
                    report["blocking_issues"].append(
                        f"{table} has required unhandled columns: {', '.join(unhandled)}"
                    )
                for foreign_key in report["tables"][table]["foreign_keys"]:
                    if foreign_key["referenced_table"].lower() in FORBIDDEN_WRITE_TABLES:
                        report["blocking_issues"].append(
                            f"{table}.{foreign_key['column']} requires an existing "
                            f"{foreign_key['referenced_table']}."
                        )
            report["compatible"] = not report["blocking_issues"]
            return report
        finally:
            cursor.close()
            connection.rollback()
            connection.close()

    @app.get("/api/v1/mappings")
    def list_mappings(_: None = Depends(auth)) -> Dict[str, Any]:
        return mapping_store.list()

    @app.get("/api/v1/masters/suppliers")
    def search_suppliers(
        companycode: str = Query(min_length=1, max_length=6),
        query: str = Query(min_length=1),
        _: None = Depends(auth),
    ) -> Dict[str, Any]:
        connection = open_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT TOP 25 ledgerCode, ledgerName
                FROM dbo.MasterAccountsLedger
                WHERE companyCode = ? AND ledgerName LIKE ?
                ORDER BY ledgerName
                """,
                companycode,
                f"%{query.strip()}%",
            )
            return {
                "results": [
                    {"suppliercode": str(row[0]).strip(), "supplier_name": str(row[1]).strip()}
                    for row in cursor.fetchall()
                ]
            }
        finally:
            cursor.close()
            connection.rollback()
            connection.close()

    @app.get("/api/v1/masters/items")
    def search_items(
        query: str = Query(min_length=1),
        _: None = Depends(auth),
    ) -> Dict[str, Any]:
        connection = open_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT TOP 25 itemcode, itemname, ml, packing, strengthname
                FROM dbo.itemmst
                WHERE itemname LIKE ? OR itemcode LIKE ?
                ORDER BY itemname
                """,
                f"%{query.strip()}%",
                f"%{query.strip()}%",
            )
            return {
                "results": [
                    {
                        "itemcode": str(row[0]).strip(),
                        "item_name": str(row[1]).strip(),
                        "ml": row[2],
                        "packing": row[3],
                        "strength_name": row[4],
                    }
                    for row in cursor.fetchall()
                ]
            }
        finally:
            cursor.close()
            connection.rollback()
            connection.close()

    @app.get("/api/v1/masters/items/{itemcode}/stock")
    def item_live_stock(
        itemcode: str,
        companycode: str = Query(min_length=1, max_length=6),
        yearcode: str = Query(min_length=1, max_length=6),
        _: None = Depends(auth),
    ) -> Dict[str, Any]:
        if not settings.item_stock_lookup_sql:
            return {
                "configured": False,
                "itemcode": itemcode,
                "message": "Live-stock query is not configured for this ERP installation.",
            }
        require_read_only_sql(settings.item_stock_lookup_sql)
        connection = open_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(settings.item_stock_lookup_sql, itemcode, companycode, yearcode)
            columns = [str(column[0]) for column in cursor.description or []]
            return {
                "configured": True,
                "itemcode": itemcode,
                "results": [dict(zip(columns, row)) for row in cursor.fetchall()],
            }
        finally:
            cursor.close()
            connection.rollback()
            connection.close()

    @app.post("/api/v1/mappings/suppliers")
    def save_supplier_mapping(
        request: SupplierMappingRequest,
        _: None = Depends(auth),
    ) -> Dict[str, Any]:
        source_name = request.source_name.strip()
        target_name = request.target_name.strip()
        connection = open_connection()
        cursor = connection.cursor()
        try:
            suppliercode = db.lookup_single_value(
                cursor,
                settings.supplier_lookup_sql,
                (target_name, request.companycode),
                f"Supplier '{target_name}'",
            )
        finally:
            cursor.close()
            connection.rollback()
            connection.close()
        mapping_store.set_supplier(source_name, target_name)
        logger.info("Saved supplier mapping source=%s target=%s.", source_name, target_name)
        return {
            "saved": True,
            "source_name": source_name,
            "target_name": target_name,
            "suppliercode": suppliercode,
        }

    @app.delete("/api/v1/mappings/suppliers")
    def delete_supplier_mapping(
        source_name: str = Query(min_length=1),
        _: None = Depends(auth),
    ) -> Dict[str, Any]:
        return {"deleted": mapping_store.delete_supplier(source_name.strip())}

    @app.post("/api/v1/mappings/items")
    def save_item_mapping(
        request: ItemMappingRequest,
        _: None = Depends(auth),
    ) -> Dict[str, Any]:
        source_name = request.source_name.strip()
        batch = (request.batch or "").strip()
        item_code = request.item_code.strip()
        if request.ml is None and not batch:
            raise HTTPException(status_code=422, detail="Item mapping requires ml or legacy batch.")
        connection = open_connection()
        cursor = connection.cursor()
        try:
            verified_code = db.lookup_single_value(
                cursor,
                settings.item_code_verify_sql,
                (item_code,),
                f"Item code '{item_code}'",
            )
        finally:
            cursor.close()
            connection.rollback()
            connection.close()
        normalized_name = " ".join(source_name.upper().split())
        mapping_key = (
            f"{normalized_name}|ML:{request.ml:.2f}"
            if request.ml is not None
            else f"{source_name}|{batch}"
        )
        mapping_store.set_item(mapping_key, verified_code)
        logger.info("Saved item mapping key=%s code=%s.", mapping_key, verified_code)
        return {"saved": True, "mapping_key": mapping_key, "item_code": verified_code}

    @app.post("/api/v1/resolutions/{resolution_id}/retry")
    def retry_resolution(resolution_id: str, _: None = Depends(auth)) -> Any:
        state = store.get_resolution(resolution_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Resolution session not found or expired.")
        payload = state["payload_json"]
        connection = open_connection()
        try:
            prepared = prepare_invoice_previews(
                payload["invoices"],
                connection,
                companycode=payload["companycode"],
                yearcode=payload["yearcode"],
                strict_total=payload["strict_total"],
                file_hash=payload.get("file_hash"),
            )
            return JSONResponse(
                status_code=prepared["status_code"],
                content=jsonable_encoder(prepared["body"]),
            )
        finally:
            connection.close()

    @app.delete("/api/v1/mappings/items")
    def delete_item_mapping(
        mapping_key: str = Query(min_length=1),
        _: None = Depends(auth),
    ) -> Dict[str, Any]:
        return {"deleted": mapping_store.delete_item(mapping_key.strip())}

    @app.post("/api/v1/purchases/preview")
    def preview(request: PurchasePreviewRequest, _: None = Depends(auth)) -> Dict[str, Any]:
        connection = open_connection()
        try:
            preview_data = preview_purchase(
                request.invoice,
                connection,
                companycode=request.companycode,
                yearcode=request.yearcode,
                mapping_config=current_mapping_config(),
                supplier_lookup_sql=settings.supplier_lookup_sql,
                item_lookup_sql=settings.item_lookup_sql,
                item_code_verify_sql=settings.item_code_verify_sql,
                strict_total=request.strict_total,
            )
        finally:
            connection.close()

        approval = create_preview_approval(
            request.invoice,
            request.companycode,
            request.yearcode,
            request.strict_total,
            preview_data,
        )
        logger.info(
            "Preview approved id=%s docno=%s company=%s year=%s.",
            approval["preview_id"],
            preview_data["docno"],
            request.companycode,
            request.yearcode,
        )
        return approval

    @app.post("/api/v1/purchases/insert")
    def insert(request: PurchaseInsertRequest, _: None = Depends(auth)) -> Dict[str, Any]:
        token = verify_approval_token(request.approval_token, settings.approval_secret)
        preview_id = token["preview_id"]
        state = store.claim_for_insert(preview_id)
        if state["status"] == "inserted":
            return {
                "success": True,
                "idempotent_replay": True,
                "preview_id": preview_id,
                "result": state["result_json"],
            }
        if state["digest"] != token["digest"] or payload_digest(state["payload_json"]) != token["digest"]:
            store.mark_failed(preview_id, "Approval payload digest mismatch.")
            raise HTTPException(status_code=400, detail="Approval payload integrity check failed.")

        payload = state["payload_json"]
        connection = open_connection()
        try:
            result = insert_purchase(
                payload["invoice"],
                connection,
                companycode=payload["companycode"],
                yearcode=payload["yearcode"],
                mapping_config=current_mapping_config(),
                supplier_lookup_sql=settings.supplier_lookup_sql,
                item_lookup_sql=settings.item_lookup_sql,
                item_code_verify_sql=settings.item_code_verify_sql,
                strict_total=payload["strict_total"],
                transaction_type=settings.transaction_type,
                usercode=settings.usercode,
                sync=settings.sync,
            )
            store.mark_inserted(preview_id, result)
            logger.info("Inserted preview id=%s trnid=%s trnno=%s.", preview_id, result["trnid"], result["trnno"])
            return {
                "success": True,
                "idempotent_replay": False,
                "preview_id": preview_id,
                "result": result,
            }
        except Exception as exc:
            store.mark_failed(preview_id, str(exc))
            raise
        finally:
            connection.close()

    @app.get("/api/v1/approvals/{preview_id}")
    def approval_status(preview_id: str, _: None = Depends(auth)) -> Dict[str, Any]:
        state = store.get(preview_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Preview not found.")
        return {
            "preview_id": preview_id,
            "status": state["status"],
            "created_at": state["created_at"],
            "expires_at": state["expires_at"],
            "preview": state["preview_json"],
            "result": state["result_json"],
            "error": state["error"],
        }

    return app
