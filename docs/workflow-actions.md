# Workflow actions

Deploy or choose a Docling REST service separately; it is not bundled. Save its base URL and optional API key in a project Docling connection and select it on the step. Public endpoints require HTTPS; private endpoints need the host private-network policy.

Map a document artifact from an earlier step into Source document artifact. Start with automatic format detection and server defaults. Pass markdown to a text step or markdown_artifact_handle to a file step; if markdown_truncated is true, use the complete Markdown artifact.

Select the named action in an Extension tools step. Map workflow values to its inputs and map the returned fields needed by later steps. Examples below contain sample data; replace artifact handles and embedding values with actual earlier-step outputs.

## Convert a document to Markdown and structured JSON

Send one document artifact to the selected Docling service and save Markdown and Docling JSON artifacts. Returns inline Markdown, preview and truncation status; input is limited to 20 MiB and the complete text remains in the Markdown artifact.

- **Source document artifact**: Map a document artifact from an earlier workflow step, up to 20 MiB. The selected Docling service must support its format.
- **Source document format**: Leave blank for automatic detection. Explicit options follow docling-serve 1.38.2; support can differ on other server versions.
- **Enable OCR**: Optional setting forwarded to Docling. Enable for scanned text; leave unset to use the service default.
- **Force OCR on all text**: Optional setting forwarded to Docling. Use when existing text extraction is unsuitable; leave unset to use the service default.
- **OCR language**: Optional OCR language code forwarded to the service. Supported codes depend on its configured OCR engine; leave blank for its default.
- **Table recognition mode**: Choose fast or accurate when supported by the service. Leave blank to use the Docling service default.

Example input values:

```json
{
  "artifact_handle": "artifact:input",
  "ocr_lang": "en"
}
```

In the workflow editor, select the source artifact from the earlier step. `artifact:input` illustrates the mapped handle; replace it with the actual handle supplied by that step.
