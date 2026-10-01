# Docling Document Extraction Extension

`flowsteward.docling` is a generic tool-provider extension that sends a workflow
document artifact to an external Docling REST API and writes two workflow
artifacts:

- `document.md` (`text/markdown`)
- `document.docling.json` (`application/json`)

It only performs document conversion. It does not contain document-specific
schemas, field extraction, or business rules.

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

- `from_format`
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
