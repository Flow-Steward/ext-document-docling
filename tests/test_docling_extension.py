from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BUNDLE_ROOT.parents[1]
if str(BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(BUNDLE_ROOT))


def _payload(
    *,
    base_url: str,
    artifact_handle: str = "artifact:input_doc",
    api_key: str = "",
    request_timeout_seconds: int = 30,
    input_size_bytes: int = 12,
    outputs: list[dict[str, Any]] | None = None,
    input_body_path: Path | None = None,
) -> dict[str, Any]:
    input_path = input_body_path or Path("/dev/null")
    return {
        "contract_version": "extension_host_v1",
        "runtime_context": {},
        "action": {
            "action_id": "convert_document",
            "target": {
                "connection": {
                    "connection_type_id": "docling",
                    "config": {
                        "base_url": base_url,
                        "request_timeout_seconds": request_timeout_seconds,
                    },
                    "secrets": {"api_key": api_key} if api_key else {},
                }
            },
            "input": {
                "artifact_handle": artifact_handle,
                "from_format": "pdf",
                "do_ocr": True,
                "force_ocr": False,
                "ocr_lang": "en",
                "table_mode": "fast",
            },
        },
        "artifacts": {
            "inputs": [
                {
                    "kind": "artifact",
                    "artifact_id": artifact_handle.removeprefix("artifact:"),
                    "artifact_handle": artifact_handle,
                    "binding_key": "source_document",
                    "filename": "source.pdf",
                    "size_bytes": input_size_bytes,
                    "access": {
                        "transport": "presigned_url",
                        "mode": "read",
                        "download_url": input_path.as_uri(),
                        "max_size_bytes": input_size_bytes,
                    },
                }
            ],
            "outputs": outputs
            or [
                _output_descriptor(
                    artifact_id="md_out",
                    handle="artifact:md_out",
                    binding_key="markdown_artifact_handle",
                    filename="document.md",
                    content_type="text/markdown",
                ),
                _output_descriptor(
                    artifact_id="json_out",
                    handle="artifact:json_out",
                    binding_key="docling_json_artifact_handle",
                    filename="document.docling.json",
                    content_type="application/json",
                ),
            ],
        },
    }


def _output_descriptor(
    *,
    artifact_id: str,
    handle: str,
    binding_key: str,
    filename: str,
    content_type: str,
) -> dict[str, Any]:
    path = BUNDLE_ROOT / ".test-artifacts" / f"{artifact_id}-{filename}"
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        path.unlink()
    return {
        "kind": "artifact",
        "artifact_id": artifact_id,
        "artifact_handle": handle,
        "binding_key": binding_key,
        "filename": filename,
        "content_type": content_type,
        "access": {
            "transport": "presigned_url",
            "mode": "write",
            "upload_url": path.as_uri(),
            "max_size_bytes": 50 * 1024 * 1024,
        },
        "_path": str(path),
    }


class _DoclingHandler(BaseHTTPRequestHandler):
    response_payload: ClassVar[dict[str, Any]] = {
        "document": {
            "md_content": "# Extracted\n\nHello Docling",
            "json_content": {"schema_name": "DoclingDocument", "body": {"children": []}},
        },
        "status": "success",
        "processing_time": 1.25,
        "timings": {"convert": 1.2},
        "errors": [],
    }
    requests: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length)
        self.__class__.requests.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "body": body,
            }
        )
        encoded = json.dumps(self.__class__.response_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: Any) -> None:
        return None


@pytest.fixture
def docling_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "1")
    _DoclingHandler.requests = []
    _DoclingHandler.response_payload = {
        "document": {
            "md_content": "# Extracted\n\nHello Docling",
            "json_content": {"schema_name": "DoclingDocument", "body": {"children": []}},
        },
        "status": "success",
        "processing_time": 1.25,
        "timings": {"convert": 1.2},
        "errors": [],
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DoclingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _DoclingHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_convert_document_posts_once_and_writes_markdown_and_json_artifacts(
    tmp_path: Path,
    docling_server,
):
    import docling_extension

    base_url, handler = docling_server
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF test")
    payload = _payload(base_url=base_url, api_key="secret-key", input_body_path=source)

    response = docling_extension.handle_payload(payload)

    assert response["ok"] is True
    result = response["result"]
    assert result["provider_status"] == "success"
    assert result["markdown_preview"] == "# Extracted\n\nHello Docling"
    assert result["markdown_truncated"] is False
    assert result["processing_time"] == 1.25
    assert result["timings"] == {"convert": 1.2}
    assert result["markdown_artifact_handle"] == "artifact:md_out"
    assert result["docling_json_artifact_handle"] == "artifact:json_out"
    assert (BUNDLE_ROOT / ".test-artifacts" / "md_out-document.md").read_text() == (
        "# Extracted\n\nHello Docling"
    )
    stored_json = json.loads(
        (BUNDLE_ROOT / ".test-artifacts" / "json_out-document.docling.json").read_text()
    )
    assert stored_json["schema_name"] == "DoclingDocument"

    assert len(handler.requests) == 1
    request = handler.requests[0]
    assert request["path"] == "/v1/convert/file"
    assert request["headers"]["X-Api-Key"] == "secret-key"
    body = request["body"]
    assert b'name="to_formats"\r\n\r\nmd' in body
    assert b'name="to_formats"\r\n\r\njson' in body
    assert b'name="image_export_mode"\r\n\r\nplaceholder' in body
    assert b'name="from_formats"\r\n\r\npdf' in body
    assert b'name="do_ocr"\r\n\r\ntrue' in body


def test_convert_document_reports_each_stage_before_it_runs(
    tmp_path: Path,
    docling_server,
    monkeypatch: pytest.MonkeyPatch,
):
    import docling_extension

    base_url, handler = docling_server
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF test")
    stages: list[tuple[str, int]] = []
    monkeypatch.setattr(
        docling_extension,
        "report_progress",
        lambda message="", **_kwargs: stages.append((message, len(handler.requests))),
    )

    response = docling_extension.handle_payload(
        _payload(base_url=base_url, api_key="secret-key", input_body_path=source)
    )

    assert response["ok"] is True
    assert stages == [
        ("Reading the document", 0),
        ("Converting the document (1 KB) with Docling; this can take a few minutes", 0),
        ("Saving the Markdown and Docling JSON files", 1),
    ]


def test_private_docling_url_is_rejected_before_artifact_read_or_api_key_send(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    import docling_extension

    monkeypatch.delenv("FS_ALLOW_PRIVATE_REMOTE_URLS", raising=False)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF test")
    read_attempt = False

    def fail_read(*_args: Any, **_kwargs: Any) -> bytes:
        nonlocal read_attempt
        read_attempt = True
        raise AssertionError("artifact body must not be read before URL preflight")

    monkeypatch.setattr(docling_extension, "read_artifact_bytes", fail_read)

    response = docling_extension.handle_payload(
        _payload(
            base_url="http://127.0.0.1:5001",
            api_key="secret-key",
            input_body_path=source,
        )
    )

    assert response["ok"] is False
    assert response["error_code"] == "network_blocked"
    assert read_attempt is False


def test_convert_document_uses_preflight_pins_for_provider_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    import docling_extension

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF test")
    captured: dict[str, Any] = {}

    class _Response:
        status = 200

        def __init__(self) -> None:
            self._read = False

        def read(self, _size: int = -1) -> bytes:
            if self._read:
                return b""
            self._read = True
            return json.dumps(
                {
                    "document": {"md_content": "ok", "json_content": {}},
                    "status": "success",
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    def _open(request: Any, **kwargs: Any) -> _Response:
        captured["url"] = request.full_url
        captured["pinned_ips"] = kwargs.get("pinned_ips")
        return _Response()

    monkeypatch.setattr(
        docling_extension,
        "_preflight_docling_url",
        lambda _url: ("https://docling.example", ("203.0.113.10",)),
    )
    monkeypatch.setattr(docling_extension, "open_pinned_url", _open)

    response = docling_extension.handle_payload(
        _payload(base_url="https://docling.example", input_body_path=source)
    )

    assert response["ok"] is True
    assert captured == {
        "url": "https://docling.example/v1/convert/file",
        "pinned_ips": ("203.0.113.10",),
    }


@pytest.mark.parametrize(
    ("url", "resolved_ips", "allowed"),
    [
        ("http://host.docker.internal:5001", ["192.168.65.2"], False),
        ("http://docling:5001", ["172.18.0.10"], False),
        ("http://host.docker.internal:5001", ["192.168.65.2"], True),
        ("http://docling:5001", ["172.18.0.10"], True),
    ],
)
def test_docling_url_policy_covers_docker_hosts(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    resolved_ips: list[str],
    allowed: bool,
):
    import docling_extension

    if allowed:
        monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "1")
    else:
        monkeypatch.delenv("FS_ALLOW_PRIVATE_REMOTE_URLS", raising=False)
    monkeypatch.setattr(
        docling_extension,
        "resolve_pinned_ips",
        lambda *_args, **_kwargs: list(resolved_ips),
    )

    if allowed:
        assert docling_extension.preflight_docling_url(url) == url
    else:
        with pytest.raises(docling_extension.DoclingError, match="network_blocked"):
            docling_extension.preflight_docling_url(url)


def test_partial_success_requires_both_outputs(tmp_path: Path, docling_server):
    import docling_extension

    base_url, handler = docling_server
    handler.response_payload = {
        "document": {"md_content": "# Extracted"},
        "status": "partial_success",
        "processing_time": 0.5,
        "timings": {},
        "errors": [{"message": "json export missing"}],
    }
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF test")

    response = docling_extension.handle_payload(_payload(base_url=base_url, input_body_path=source))

    assert response["ok"] is False
    assert response["error_code"] == "missing_docling_json"
    assert response["result"]["provider_status"] == "partial_success"
    assert response["result"]["errors"] == [
        {"code": "provider_error", "message": "json export missing"}
    ]


def test_success_with_empty_markdown_writes_zero_byte_artifact(tmp_path: Path, docling_server):
    import docling_extension

    base_url, handler = docling_server
    handler.response_payload = {
        "document": {
            "md_content": "",
            "json_content": {"schema_name": "DoclingDocument", "body": {"children": []}},
        },
        "status": "success",
        "processing_time": 0.2,
        "timings": {},
        "errors": [],
    }
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF test")

    response = docling_extension.handle_payload(_payload(base_url=base_url, input_body_path=source))

    assert response["ok"] is True
    assert response["result"]["markdown_preview"] == ""
    assert response["result"]["markdown_truncated"] is False
    assert (BUNDLE_ROOT / ".test-artifacts" / "md_out-document.md").read_bytes() == b""


def test_provider_failure_status_precedes_missing_outputs_and_normalizes_docling_errors(
    tmp_path: Path,
    docling_server,
):
    import docling_extension

    base_url, handler = docling_server
    handler.response_payload = {
        "document": {},
        "status": "failure",
        "processing_time": 0.1,
        "timings": {},
        "errors": [
            {
                "category": "input_format",
                "error_message": "unsupported image encoding",
            }
        ],
    }
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF test")

    response = docling_extension.handle_payload(_payload(base_url=base_url, input_body_path=source))

    assert response["ok"] is False
    assert response["error_code"] == "provider_failed"
    assert response["result"]["errors"] == [
        {"code": "input_format", "message": "unsupported image encoding"}
    ]


def test_inline_provider_metadata_is_bounded(tmp_path: Path, docling_server):
    import docling_extension

    base_url, handler = docling_server
    handler.response_payload = {
        "document": {
            "md_content": "ok",
            "json_content": {"schema_name": "DoclingDocument"},
        },
        "status": "success",
        "processing_time": 0.1,
        "timings": {
            "oversized": "x" * (6 * 1024 * 1024),
            "nested": {
                f"branch_{idx}": {f"leaf_{leaf}": "x" * 1000 for leaf in range(10)}
                for idx in range(10)
            },
            **{f"step_{idx}": idx for idx in range(40)},
        },
        "errors": [{"code": "c" * 400, "message": "m" * 2000}],
    }
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF test")

    response = docling_extension.handle_payload(_payload(base_url=base_url, input_body_path=source))

    encoded = json.dumps(response, ensure_ascii=False)
    assert len(encoded.encode("utf-8")) <= 64 * 1024
    assert len(response["result"]["timings"]) <= 20
    assert len(response["result"]["timings"]["oversized"]) <= 1000
    assert len(response["result"]["errors"][0]["code"]) <= 128
    assert len(response["result"]["errors"][0]["message"]) <= 1000


def test_main_returns_nonzero_for_error_envelope(tmp_path: Path):
    payload = {
        "action": {
            "action_id": "convert_document",
            "input": {},
            "target": {"connection": {"config": {}}},
        }
    }

    proc = subprocess.run(
        [sys.executable, "main.py"],
        cwd=BUNDLE_ROOT,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 2
    response = json.loads(proc.stdout)
    assert response["ok"] is False
    assert response["error_code"] == "invalid_payload"


def test_main_handles_malformed_stdin_without_traceback():
    proc = subprocess.run(
        [sys.executable, "main.py"],
        cwd=BUNDLE_ROOT,
        input="{not-json",
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 2
    assert proc.stderr == ""
    response = json.loads(proc.stdout)
    assert response["ok"] is False
    assert response["error_code"] == "invalid_json"


def test_input_artifact_size_is_capped_before_provider_call(tmp_path: Path, docling_server):
    import docling_extension

    base_url, handler = docling_server
    source = tmp_path / "source.pdf"
    source.write_bytes(b"not actually huge")

    response = docling_extension.handle_payload(
        _payload(
            base_url=base_url,
            input_body_path=source,
            input_size_bytes=20 * 1024 * 1024 + 1,
        )
    )

    assert response["ok"] is False
    assert response["error_code"] == "input_too_large"
    assert handler.requests == []
