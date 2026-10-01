from __future__ import annotations

from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_declares_docling_connector_contract() -> None:
    manifest = (BUNDLE_ROOT / "extension.yaml").read_text()
    operations = (BUNDLE_ROOT / "contracts/operation_manifest.yaml").read_text()
    policies = (BUNDLE_ROOT / "contracts/artifact_policies.yaml").read_text()
    connections = (BUNDLE_ROOT / "contracts/connection_types.yaml").read_text()

    assert "extension_id: flowsteward.docling" in manifest
    assert "kind: tool_provider" in manifest
    assert "timeout_seconds: 300" in manifest
    assert "step_ui_manifest: contracts/step_ui_manifest.yaml" in manifest
    assert "- artifact:read" in manifest
    assert "- artifact:write" in manifest
    assert "binding_key: markdown_artifact_handle" in policies
    assert "content_type: text/markdown" in policies
    assert "binding_key: docling_json_artifact_handle" in policies
    assert "content_type: application/json" in policies
    assert "operation_id: convert_document" in operations
    assert "connection_type_ids: [docling]" in operations
    assert "name: artifact_handle" in operations
    assert "value_type: artifact_handle" in operations
    assert "invoice" not in (BUNDLE_ROOT / "README.md").read_text().lower()
    assert "connection_type_id: docling" in connections
    assert "- base_url" in connections
    assert "api_key:" in connections


def test_step_ui_manifest_declares_workflow_form_controls() -> None:
    step_ui = (BUNDLE_ROOT / "contracts/step_ui_manifest.yaml").read_text()

    assert "operation_id: convert_document" in step_ui
    assert "name: artifact_handle" in step_ui
    assert "widget_type: artifact_picker" in step_ui
    assert "required: true" in step_ui
    assert "name: do_ocr" in step_ui
    assert "name: force_ocr" in step_ui
    assert "name: ocr_lang" in step_ui
    assert "name: table_mode" in step_ui
    assert "widget_type: boolean" in step_ui
    assert "widget_type: select" in step_ui
    assert "value: fast" in step_ui
    assert "value: accurate" in step_ui


def test_operation_outputs_use_the_same_binding_keys_as_artifact_policy() -> None:
    operation = (BUNDLE_ROOT / "contracts/operation_manifest.yaml").read_text()
    policies = (BUNDLE_ROOT / "contracts/artifact_policies.yaml").read_text()

    assert "name: markdown_artifact_handle" in operation
    assert "name: docling_json_artifact_handle" in operation
    assert "binding_key: markdown_artifact_handle" in policies
    assert "binding_key: docling_json_artifact_handle" in policies


def test_timeout_budget_leaves_entrypoint_headroom() -> None:
    source = (BUNDLE_ROOT / "docling_extension.py").read_text()

    assert "ARTIFACT_IO_TIMEOUT_SECONDS = 10" in source


def test_manifest_declares_workflow_visible_convert_document_action() -> None:
    manifest = (BUNDLE_ROOT / "extension.yaml").read_text()
    actions = (BUNDLE_ROOT / "ui" / "actions" / "actions.yaml").read_text()

    assert "action_manifest: ui/actions/actions.yaml" in manifest
    assert "action_id: convert_document" in actions
    assert "workflow_visible: true" in actions
    assert "- markdown_artifact_handle" in actions
    assert "- docling_json_artifact_handle" in actions


def test_about_page_uses_the_existing_static_page_contract() -> None:
    ui_manifest = (BUNDLE_ROOT / "ui" / "ui_manifest.yaml").read_text()
    about_path = BUNDLE_ROOT / "ui" / "pages" / "about.yaml"

    assert ui_manifest.count("- ref:") == 2
    assert "ref: pages/about.yaml" in ui_manifest
    assert "ref: pages/configuration.yaml" in ui_manifest
    assert "pages/overview.yaml" not in ui_manifest
    assert about_path.exists()

    about = about_path.read_text()
    assert "page_id: about" in about
    assert "title: About" in about
    assert "type: markdown" in about
    assert "requires_project" not in about
    assert "https://docling-project.github.io/" in about
    assert "cloud-hosted or self-hosted" in about
    assert "Markdown" in about
    assert "Docling JSON" in about
    assert "business rules stay in your workflow" in about


def test_configuration_page_manages_project_scoped_docling_connections() -> None:
    page = (BUNDLE_ROOT / "ui" / "pages" / "configuration.yaml").read_text()
    form = (BUNDLE_ROOT / "ui" / "components" / "connection_form.yaml").read_text()

    assert "page_id: configuration" in page
    assert "ref: ../components/connection_form.yaml" in page
    assert "type: connection_form" in form
    assert "connection_kind: extension" in form
    assert "connection_type: docling" in form
    # config/secret fields must match contracts/connection_types.yaml schemas
    assert "- base_url" in form
    assert "- request_timeout_seconds" in form
    assert "- api_key" in form
    assert "widget: secret" in form
