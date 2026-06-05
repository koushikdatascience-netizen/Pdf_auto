"""FastAPI application for the local ERP integration agent."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

import db
from extract_pdf_json import extract_pdf
from mapping_service import MappingError
from pdf_purchase_adapter import PdfPurchaseAdapterError, normalize_extracted_purchases
from purchase_service import insert_purchase, preview_purchase
from purchase_service import PurchaseServiceError
from schema_check import (
    EXPECTED_INSERT_COLUMNS,
    FORBIDDEN_WRITE_TABLES,
    inspect_foreign_keys,
    inspect_table,
)
from validation import ValidationError

from .config import Settings, load_settings
from .models import PurchaseInsertRequest, PurchasePreviewRequest
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
        version="1.2.0",
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.audit_handler = audit_handler

    def open_connection() -> Any:
        if not settings.connection_string:
            raise HTTPException(status_code=503, detail="Database connection is not configured.")
        try:
            return db.connect(settings.connection_string)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Database connection failed: {exc}") from exc

    async def save_pdf_upload(pdf: UploadFile) -> tuple[str, Path]:
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
        return filename, path

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
    ) -> Dict[str, Any]:
        payload = {
            "companycode": companycode,
            "yearcode": yearcode,
            "invoice": invoice,
            "strict_total": strict_total,
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
        filename, path = await save_pdf_upload(pdf)
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
        filename, path = await save_pdf_upload(pdf)
        connection = open_connection()
        try:
            extracted = extract_pdf(path)
            invoices = normalize_extracted_purchases(extracted)
            preview_results = []
            for invoice in invoices:
                preview_results.append(
                    (
                        invoice,
                        preview_purchase(
                            invoice,
                            connection,
                            companycode=companycode,
                            yearcode=yearcode,
                            mapping_config=settings.mapping_config,
                            supplier_lookup_sql=settings.supplier_lookup_sql,
                            item_lookup_sql=settings.item_lookup_sql,
                            item_code_verify_sql=settings.item_code_verify_sql,
                            strict_total=strict_total,
                        ),
                    )
                )
            approvals = [
                create_preview_approval(
                    invoice,
                    companycode,
                    yearcode,
                    strict_total,
                    preview_data,
                )
                for invoice, preview_data in preview_results
            ]
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

    @app.post("/api/v1/purchases/preview")
    def preview(request: PurchasePreviewRequest, _: None = Depends(auth)) -> Dict[str, Any]:
        connection = open_connection()
        try:
            preview_data = preview_purchase(
                request.invoice,
                connection,
                companycode=request.companycode,
                yearcode=request.yearcode,
                mapping_config=settings.mapping_config,
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
                mapping_config=settings.mapping_config,
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
