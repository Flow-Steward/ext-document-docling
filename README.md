# Docling Document Extraction Extension

Requires extension runtime contract 1.4.0 or later for typed workflow inputs
(`runtime.compatibility.platform_min`; compatibility is checked during
installation). This is the host contract version, not the Flow Steward product
version.

Prepare documents for agents, classification and review with Docling Document Extraction. It sends a workflow document artifact to an external Docling REST service and returns Markdown plus Docling JSON. It writes both outputs as workflow artifacts, giving later steps readable text and the service's structured document representation. The result also includes inline Markdown, a preview, the full character count and a flag showing whether the inline text was shortened.

The conversion action supports optional source-format, OCR, OCR-language and table-processing settings. Use it to prepare documents for an agent, pass extracted text into a classification or review step, or preserve converted files for downstream processing. Document-specific field extraction and business rules can be implemented in later workflow steps.

You can connect a public managed endpoint or a separately deployed Docling service on your own infrastructure. Docling itself is not bundled with the extension. Input documents are limited to 20 MiB, and each output artifact to 50 MiB. Large inline Markdown is shortened while the Markdown artifact retains the complete conversion.

- `document.md` (`text/markdown`)
- `document.docling.json` (`application/json`)

The operation result also carries the document text itself:

- `markdown`: the whole converted Markdown, for the next workflow step. Text
  beyond 3 MiB (JSON-encoded) is cut so the result stays under Flow Steward's
  5 MiB extension output limit; `document.md` always holds all of it.
- `markdown_chars`: the length of the whole Markdown in characters.
- `markdown_truncated`: `true` only when `markdown` was cut at that limit.
- `markdown_preview`: the first 4,000 characters, for display.

It only performs document conversion. It does not contain document-specific
schemas, field extraction, or business rules.

## Requirements

This release speaks the host contract `extension_host_v2` with its final
execution scope (`group_id`, `for_each_id`, `step_id`, `step_execution_id`)
and is built on `flowsteward-extension-sdk` 0.4.0. A request with another
contract, a missing scope key, or both a Group and a For each as its owner is
refused before the document is read or sent. Install it on a Flow Steward Core
that speaks this contract; an older Core does not run it.

Logo: `icon.png` uses the official Docling mark. See [BRANDING.md](BRANDING.md)
for its source and attribution.

## Connection

Create a project connection with:

- `base_url`: Docling service base URL.
- optional `api_key`: stored as a connection secret and sent only as
  `X-Api-Key`.
- optional `request_timeout_seconds`: clamped to 1-240 seconds.

Public Docling services must use HTTPS. Private HTTP/HTTPS endpoints are allowed
only when the Flow Steward host policy `FS_ALLOW_PRIVATE_REMOTE_URLS=1` is set.

## Deployment Patterns

Cloud or managed public endpoint:

```text
https://docling.example.com
```

Docling in a separate Docker Compose on the same machine, reached from Flow
Steward containers through Docker Desktop:

```text
http://host.docker.internal:5001
```

On Linux, add host gateway mapping to the Flow Steward services that invoke
extensions:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

Docling in a shared external Docker network:

```text
http://docling:5001
```

Local host endpoint:

```text
http://127.0.0.1:5001
```

For any private, loopback, link-local, or reserved endpoint, set:

```env
FS_ALLOW_PRIVATE_REMOTE_URLS=1
```

Then recreate both runtime services so the environment policy is applied:

```bash
docker compose -f infra/docker/docker-compose.yaml up -d --force-recreate fs-app fs-worker
```

Important: `FS_ALLOW_PRIVATE_REMOTE_URLS` is a global Flow Steward host policy.
It opens private network access to every extension call that uses the standard
HTTP SDK, not only to this Docling extension.

## Operation

`convert_document` accepts only `artifact_handle` for document input. It sends a
multipart request to `/v1/convert/file`, always requests `to_formats=md,json`,
and uses `image_export_mode=placeholder`.

Optional settings:

- `from_format` (optional; leave unset for automatic detection). Explicit options follow docling-serve 1.38.2; other server versions may support a different set.
- `do_ocr`
- `force_ocr`
- `ocr_lang`
- `table_mode`

Limits:

- input artifact: 20 MiB
- Docling response: 100 MiB
- each output artifact: 50 MiB
- Docling request timeout: max 240 seconds

The extension does not follow redirects and relies on the Flow Steward extension
SDK for URL validation, DNS pinning, and peer verification.

## Workflow action setup and examples

See [Workflow actions](docs/workflow-actions.md) for connection setup, inputs and practical examples.
