"""API-key authentication and signed approval-token helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict

from fastapi import Header, HTTPException, status


def require_api_key(expected_key: str):
    async def dependency(x_api_key: str = Header(default="", alias="X-API-Key")) -> None:
        if not expected_key or not hmac.compare_digest(x_api_key, expected_key):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")

    return dependency


def payload_digest(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_approval_token(preview_id: str, digest: str, expires_at: int, secret: str) -> str:
    payload = {"preview_id": preview_id, "digest": digest, "exp": expires_at}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return ".".join(
        [
            base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
            base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
        ]
    )


def verify_approval_token(token: str, secret: str) -> Dict[str, Any]:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        raw = base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
        signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature mismatch")
        payload = json.loads(raw.decode("utf-8"))
        if int(payload["exp"]) < int(time.time()):
            raise ValueError("token expired")
        return payload
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid approval token: {exc}") from exc

