from __future__ import annotations

import hashlib
import ipaddress
import json
import mimetypes
import os
import uuid
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse

from flowsteward_extension_sdk.artifacts import (
    ArtifactAccessError,
    find_artifact_descriptor,
    read_artifact_bytes,
    write_artifact_bytes,
)
from flowsteward_extension_sdk.http import (
    Request,
    open_pinned_url,
    resolve_pinned_ips,
)

_TOKEN_APPLICATION_JSON = "application/json"

INPUT_MAX_BYTES = 20 * 1024 * 1024
RESPONSE_MAX_BYTES = 100 * 1024 * 1024
OUTPUT_MAX_BYTES = 50 * 1024 * 1024
REQUEST_TIMEOUT_DEFAULT_SECONDS = 30
REQUEST_TIMEOUT_MAX_SECONDS = 240
ARTIFACT_IO_TIMEOUT_SECONDS = 10
MARKDOWN_PREVIEW_MAX_CHARS = 4000
INLINE_STRING_MAX_CHARS = 1000
INLINE_KEY_MAX_CHARS = 128
INLINE_COLLECTION_MAX_ITEMS = 20
INLINE_TIMINGS_MAX_BYTES = 24 * 1024
INLINE_TIMINGS_MAX_NODES = 100


class DoclingError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.result = dict(result or {})


def handle_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    root = _as_dict(payload)
    action = _as_dict(root.get("action"))
    action_id = _text(action.get("action_id"))
    if action_id and action_id != "convert_document":
        return _error("invalid_payload", f"Unsupported action_id '{action_id}'")
    try:
        result = convert_document(root)
        return {"ok": True, "result": result}
    except DoclingError as exc:
        response = _error(exc.code, exc.message)
        if exc.result:
            response["result"] = exc.result
        return response
    except ArtifactAccessError as exc:
        return _error("artifact_access_failed", str(exc))
    except Exception as exc:
        return _error("unexpected_error", str(exc))


def convert_document(payload: dict[str, Any]) -> dict[str, Any]:
    action = _as_dict(payload.get("action"))
    input_payload = _as_dict(action.get("input"))
    artifact_handle = _artifact_handle(input_payload)
    connection = _as_dict(_as_dict(action.get("target")).get("connection"))
    config = _as_dict(connection.get("config"))
    secrets = _as_dict(connection.get("secrets"))
    base_url, pinned_ips = _preflight_docling_url(_text(config.get("base_url")))
    _validate_input_artifact_size(payload, artifact_handle=artifact_handle)

    request_timeout = _clamp_int(
        config.get("request_timeout_seconds") or config.get("timeout_seconds"),
        default=REQUEST_TIMEOUT_DEFAULT_SECONDS,
        maximum=REQUEST_TIMEOUT_MAX_SECONDS,
    )
    document_body = read_artifact_bytes(
        payload,
        artifact_id=artifact_handle,
        binding_key="source_document",
        timeout_seconds=ARTIFACT_IO_TIMEOUT_SECONDS,
    )
    if len(document_body) > INPUT_MAX_BYTES:
        raise DoclingError("input_too_large", "Input artifact exceeds 20 MiB")
    response_payload = _post_docling_convert(
        base_url=base_url,
        document_body=document_body,
        input_descriptor=find_artifact_descriptor(
            payload,
            artifact_id=artifact_handle,
            binding_key="source_document",
            role="input",
        ),
        api_key=_text(secrets.get("api_key")),
        options=_docling_options(input_payload),
        pinned_ips=pinned_ips,
        timeout_seconds=request_timeout,
    )
    return _write_outputs(payload, response_payload)


def preflight_docling_url(url: str) -> str:
    return _preflight_docling_url(url)[0]


def _preflight_docling_url(url: str) -> tuple[str, tuple[str, ...]]:
    normalized = _normalize_base_url(url)
    parsed = urlparse(normalized)
    scheme = str(parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise DoclingError("invalid_connection", "Docling base_url must use http(s)")
    if parsed.username or parsed.password:
        raise DoclingError("invalid_connection", "Docling base_url must not include credentials")
    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise DoclingError("invalid_connection", "Docling base_url must include host")
    if _is_local_hostname(host) and not _allow_private_remote_urls():
        raise DoclingError("network_blocked", f"Docling base_url host '{host}' is not allowed")
    try:
        port = int(parsed.port or (443 if scheme == "https" else 80))
        ips = resolve_pinned_ips(host, port=port, purpose="Docling base_url")
    except ValueError as exc:
        raise DoclingError("network_blocked", str(exc)) from exc

    if not _allow_private_remote_urls() and not _all_global(ips):
        raise DoclingError(
            "network_blocked",
            f"Docling base_url host '{host}' resolves to disallowed address",
        )
    if scheme == "http" and not _all_private_or_local(ips):
        raise DoclingError(
            "insecure_public_http",
            "Public Docling endpoints must use HTTPS",
        )
    return normalized, tuple(ips)


def _post_docling_convert(
    *,
    base_url: str,
    document_body: bytes,
    input_descriptor: dict[str, Any],
    api_key: str,
    options: dict[str, str],
    pinned_ips: tuple[str, ...],
    timeout_seconds: int,
) -> dict[str, Any]:
    url = urljoin(f"{base_url.rstrip('/')}/", "v1/convert/file")
    boundary = f"flowsteward-{uuid.uuid4().hex}"
    body = _multipart_body(
        boundary=boundary,
        fields=options,
        file_field="files",
        filename=_safe_filename(_text(input_descriptor.get("filename")) or "document.bin"),
        content_type=_text(input_descriptor.get("content_type"))
        or mimetypes.guess_type(_text(input_descriptor.get("filename")))[0]
        or "application/octet-stream",
        file_body=document_body,
    )
    headers = {
        "Accept": _TOKEN_APPLICATION_JSON,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    if api_key:
        headers["X-Api-Key"] = api_key
    request = Request(url, data=body, method="POST", headers=headers)
    try:
        with open_pinned_url(
            request,
            timeout_seconds=float(timeout_seconds),
            purpose="Docling convert endpoint",
            pinned_ips=pinned_ips,
        ) as response:
            status = int(getattr(response, "status", 200) or 200)
            response_body = _read_limited_response(response)
    except HTTPError as exc:
        detail = _bounded_text(exc.read(4096).decode("utf-8", errors="replace"), limit=1000)
        raise DoclingError(
            "provider_http_error",
            f"Docling returned HTTP {exc.code}",
            result={
                "provider_status": "failure",
                "errors": [_error_item("provider_http_error", detail)],
            },
        ) from exc
    except (OSError, ValueError) as exc:
        raise DoclingError("provider_request_failed", str(exc)) from exc
    if status >= 400:
        raise DoclingError("provider_http_error", f"Docling returned HTTP {status}")
    try:
        parsed = json.loads(response_body.decode("utf-8"))
    except Exception as exc:
        raise DoclingError("invalid_provider_response", "Docling response was not JSON") from exc
    if not isinstance(parsed, dict):
        raise DoclingError("invalid_provider_response", "Docling response must be a JSON object")
    return parsed


def _write_outputs(payload: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    document = _as_dict(response.get("document"))
    markdown, markdown_present = _provider_string_field(
        (document, "md_content"),
        (document, "markdown"),
        (response, "markdown"),
    )
    docling_json, docling_json_present = _provider_json_field(
        (document, "json_content"),
        (document, "json"),
        (response, "json"),
    )
    status = _bounded_text(_text(response.get("status")) or "unknown", limit=INLINE_KEY_MAX_CHARS)
    errors = _normalized_errors(response.get("errors"))
    base_result = {
        "provider_status": status,
        "processing_time": _number(response.get("processing_time")),
        "timings": _bounded_json_object(response.get("timings")),
        "errors": errors,
        "markdown_preview": _bounded_text(markdown, limit=MARKDOWN_PREVIEW_MAX_CHARS),
        "markdown_truncated": len(markdown) > MARKDOWN_PREVIEW_MAX_CHARS,
    }
    if status not in {"success", "partial_success"}:
        raise DoclingError(
            "provider_failed",
            f"Docling conversion status was '{status}'",
            result=base_result,
        )
    if not markdown_present:
        raise DoclingError(
            "missing_markdown",
            "Docling did not return Markdown content",
            result=base_result,
        )
    if not docling_json_present:
        raise DoclingError(
            "missing_docling_json",
            "Docling did not return Docling JSON content",
            result=base_result,
        )
    json_body = json.dumps(docling_json, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    markdown_body = markdown.encode("utf-8")
    if len(markdown_body) > OUTPUT_MAX_BYTES:
        raise DoclingError("output_too_large", "Markdown output exceeds 50 MiB")
    if len(json_body) > OUTPUT_MAX_BYTES:
        raise DoclingError("output_too_large", "Docling JSON output exceeds 50 MiB")
    markdown_write = write_artifact_bytes(
        payload,
        markdown_body,
        binding_key="markdown_artifact_handle",
        content_type="text/markdown",
        timeout_seconds=ARTIFACT_IO_TIMEOUT_SECONDS,
    )
    json_write = write_artifact_bytes(
        payload,
        json_body,
        binding_key="docling_json_artifact_handle",
        content_type=_TOKEN_APPLICATION_JSON,
        timeout_seconds=ARTIFACT_IO_TIMEOUT_SECONDS,
    )
    return {
        **base_result,
        "markdown_artifact_handle": _text(markdown_write.get("artifact_handle")),
        "docling_json_artifact_handle": _text(json_write.get("artifact_handle")),
        "artifacts": {
            "document.md": {
                "artifact_handle": _text(markdown_write.get("artifact_handle")),
                "mime_type": "text/markdown",
                "size_bytes": int(markdown_write.get("size_bytes") or 0),
                "sha256": _text(markdown_write.get("sha256")),
            },
            "document.docling.json": {
                "artifact_handle": _text(json_write.get("artifact_handle")),
                "mime_type": _TOKEN_APPLICATION_JSON,
                "size_bytes": int(json_write.get("size_bytes") or 0),
                "sha256": _text(json_write.get("sha256")),
            },
        },
    }


def _docling_options(input_payload: dict[str, Any]) -> dict[str, str]:
    options = {
        "to_formats": ["md", "json"],
        "image_export_mode": "placeholder",
    }
    optional_keys = {
        "from_format": "from_formats",
        "do_ocr": "do_ocr",
        "force_ocr": "force_ocr",
        "ocr_lang": "ocr_lang",
        "table_mode": "table_mode",
    }
    for source, target in optional_keys.items():
        value = input_payload.get(source)
        if value is None or value == "":
            continue
        options[target] = value
    fields: dict[str, str] = {}
    for key, value in options.items():
        if isinstance(value, list):
            fields[key] = value
        elif isinstance(value, bool):
            fields[key] = "true" if value else "false"
        else:
            fields[key] = _text(value)
    return fields


def _multipart_body(
    *,
    boundary: str,
    fields: dict[str, Any],
    file_field: str,
    filename: str,
    content_type: str,
    file_body: bytes,
) -> bytes:
    chunks: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    for key, raw_value in fields.items():
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        for value in values:
            add_field(str(key), str(value))
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("ascii"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            bytes(file_body),
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    return b"".join(chunks)


def _read_limited_response(response: Any) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > RESPONSE_MAX_BYTES:
            raise DoclingError("response_too_large", "Docling response exceeds 100 MiB")
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _validate_input_artifact_size(payload: dict[str, Any], *, artifact_handle: str) -> None:
    descriptor = find_artifact_descriptor(
        payload,
        artifact_id=artifact_handle,
        binding_key="source_document",
        role="input",
    )
    for value in (
        _as_dict(descriptor.get("access")).get("max_size_bytes"),
        descriptor.get("size_bytes"),
    ):
        size = _positive_int(value)
        if size is not None and size > INPUT_MAX_BYTES:
            raise DoclingError("input_too_large", "Input artifact exceeds 20 MiB")


def _artifact_handle(input_payload: dict[str, Any]) -> str:
    raw = input_payload.get("artifact_handle")
    if isinstance(raw, dict):
        raw = raw.get("artifact_handle") or raw.get("artifact_id")
    handle = _text(raw)
    if not handle:
        raise DoclingError("invalid_payload", "artifact_handle is required")
    return handle.removeprefix("artifact:")


def _normalize_base_url(url: str) -> str:
    normalized = _text(url).rstrip("/")
    if not normalized:
        raise DoclingError("invalid_connection", "Docling base_url is required")
    return normalized


def _all_private_or_local(ips: list[str]) -> bool:
    if not ips:
        return False
    for raw in ips:
        address = ipaddress.ip_address(str(raw).split("%", 1)[0].strip())
        if address.is_global:
            return False
    return True


def _all_global(ips: list[str]) -> bool:
    if not ips:
        return False
    return all(ipaddress.ip_address(str(raw).split("%", 1)[0].strip()).is_global for raw in ips)


def _allow_private_remote_urls() -> bool:
    return str(os.getenv("FS_ALLOW_PRIVATE_REMOTE_URLS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_local_hostname(host: str) -> bool:
    return host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local"))


def _normalized_errors(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    result: list[dict[str, str]] = []
    for row in rows[:10]:
        payload = _as_dict(row)
        code = (
            _text(payload.get("code") or payload.get("category") or payload.get("type"))
            or "provider_error"
        )
        message = _bounded_text(
            _text(
                payload.get("message")
                or payload.get("error_message")
                or payload.get("detail")
                or row
            ),
            limit=INLINE_STRING_MAX_CHARS,
        )
        result.append(_error_item(code, message))
    return result


def _error_item(code: str, message: str) -> dict[str, str]:
    return {
        "code": _bounded_text(_text(code) or "provider_error", limit=INLINE_KEY_MAX_CHARS),
        "message": _bounded_text(message, limit=INLINE_STRING_MAX_CHARS),
    }


def _provider_string_field(*candidates: tuple[dict[str, Any], str]) -> tuple[str, bool]:
    for source, key in candidates:
        if key not in source:
            continue
        value = source.get(key)
        if isinstance(value, str):
            return value, True
        return "", False
    return "", False


def _provider_json_field(*candidates: tuple[dict[str, Any], str]) -> tuple[Any, bool]:
    for source, key in candidates:
        if key in source:
            return source.get(key), source.get(key) is not None
    return None, False


def _bounded_json_object(value: Any) -> dict[str, Any]:
    budget = _InlineJsonBudget(
        max_bytes=INLINE_TIMINGS_MAX_BYTES,
        max_nodes=INLINE_TIMINGS_MAX_NODES,
    )
    bounded = _bounded_json_value(value, budget=budget)
    return bounded if isinstance(bounded, dict) else {}


class _InlineJsonBudget:
    def __init__(self, *, max_bytes: int, max_nodes: int) -> None:
        self.bytes_remaining = max_bytes
        self.nodes_remaining = max_nodes

    def consume(self, size: int) -> bool:
        if self.nodes_remaining <= 0 or self.bytes_remaining < size:
            return False
        self.nodes_remaining -= 1
        self.bytes_remaining -= size
        return True


_JSON_OMITTED = object()


def _bounded_json_value(value: Any, *, budget: _InlineJsonBudget, depth: int = 0) -> Any:
    if not budget.consume(2):
        return _JSON_OMITTED
    if depth >= 4:
        return _bounded_json_text(value, budget=budget)
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:INLINE_COLLECTION_MAX_ITEMS]:
            key = _bounded_text(raw_key, limit=INLINE_KEY_MAX_CHARS) or "field"
            if not budget.consume(len(key.encode("utf-8")) + 4):
                break
            item = _bounded_json_value(raw_value, budget=budget, depth=depth + 1)
            if item is _JSON_OMITTED:
                break
            bounded[key] = item
        return bounded
    if isinstance(value, list):
        bounded_items: list[Any] = []
        for item in value[:INLINE_COLLECTION_MAX_ITEMS]:
            bounded_item = _bounded_json_value(item, budget=budget, depth=depth + 1)
            if bounded_item is _JSON_OMITTED:
                break
            bounded_items.append(bounded_item)
        return bounded_items
    if isinstance(value, str):
        return _bounded_json_text(value, budget=budget)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return _bounded_json_text(value, budget=budget)


def _bounded_json_text(value: Any, *, budget: _InlineJsonBudget) -> str:
    text = _bounded_text(value, limit=INLINE_STRING_MAX_CHARS)
    maximum = max(0, budget.bytes_remaining - 4)
    encoded = text.encode("utf-8")[:maximum]
    bounded = encoded.decode("utf-8", errors="ignore")
    budget.consume(len(bounded.encode("utf-8")) + 2)
    return bounded


def _safe_filename(value: str) -> str:
    safe = _text(value) or "document.bin"
    return safe.replace("\\", "_").replace("/", "_").replace("\r", "_").replace("\n", "_")


def _bounded_text(value: str, *, limit: int) -> str:
    text = _text(value)
    return text[: max(0, int(limit))]


def _clamp_int(value: Any, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, maximum))


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "error": message,
        "errors": [{"code": code, "message": message}],
    }


def response_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
