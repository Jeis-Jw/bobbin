#!/usr/bin/env python3
"""context-document v1 semantic owner (Python 3.11+, stdlib only, write-free)."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import pathlib
import re
import sys
import unicodedata
import uuid
from typing import Any, Sequence


EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CONFLICT = 5
EXIT_INTEGRITY = 6
PROTOCOL = "context-common/v2"
CORE_COMPATIBLE_MAJOR = 1
REQUIRED_FEATURE = "context-owner-descriptor/v2"
DOCUMENT_INDEX = "context/document/document.index.md"
MAX_OWNER_INPUT_BYTES = 8 * 1024
MAX_CANDIDATE_BYTES = 16 * 1024
MAX_PUBLIC_OUTPUT_BYTES = 32 * 1024
MAX_PRIMARY_CLAIM_CODEPOINTS = 6000
ID_RE = re.compile(r"^ctx_[0-9a-f]{32}$")
LOCAL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
ENTRY_RE = re.compile(r"^.*<!-- context-entry (\{.*\}) -->$")
SECTIONS = ("Content",)
REQUIRED_SECTIONS = ("Content",)
LEGACY_SECTION_ALIASES: dict[str, str] = {}
PLACEHOLDERS = {"...", "TODO", "TBD", "N/A", "해당 없음"}
CANDIDATE_FIELDS = {
    "schema", "candidate_id", "title", "claim", "summary", "captured_from", "requested_kind",
    "specialized_kinds", "fallback_kind", "owner_inputs", "scope_hint", "evidence", "tags",
    "search_terms", "source_refs",
}
KNOWN_OWNER_KINDS = {"intent", "document", "term", "assumption", "observation", "decision", "snapshot"}
REQUIRED_PLUGIN = {
    "marketplace": "bobbin",
    "plugin": "bobbin",
    "selector": "bobbin@bobbin",
    "source": "Jeis-Jw/bobbin",
    "provider": "Jinwuk-Lee (Jeis-Jw)",
    "required_protocol": PROTOCOL,
    "entrypoint": "skills/context/scripts/context_cli.py",
    "compatible_major": CORE_COMPATIBLE_MAJOR,
}


class DocumentError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, exit_code: int = EXIT_USAGE):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.exit_code = exit_code

    def envelope(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message, "details": self.details}}


def compatible_core_candidates(value: str | None = None, *, minimum_version: str = "1.0.0") -> list[dict[str, str]]:
    helper = pathlib.Path(__file__).with_name("core_compatibility.py")
    spec = importlib.util.spec_from_file_location("context_document_core_compatibility", helper)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module.discover_core_candidates(
            __file__, value, compatible_major=CORE_COMPATIBLE_MAJOR, minimum_version=minimum_version
        )
    except (AttributeError, ImportError, OSError, RuntimeError, SyntaxError, TypeError, ValueError):
        return []


def _core_failure_details(value: str | None = None, **details: Any) -> dict[str, Any]:
    return {
        **details,
        "compatible_core_candidates": compatible_core_candidates(value),
        "candidate_policy": "diagnostic_only_no_automatic_substitution",
    }


def _canonical_section_name(name: str) -> str:
    return LEGACY_SECTION_ALIASES.get(name, name)


def _section_value(sections: dict[str, str], canonical: str = "Content") -> str:
    return sections.get(canonical, "")


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalized_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _canonical_slug_part(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise DocumentError("slot_invalid", f"{field} must be a string")
    folded = normalized_key(value.strip())
    output: list[str] = []
    separator = False
    for char in folded:
        if char.isalnum():
            output.append(char)
            separator = False
        elif not separator:
            output.append("-")
            separator = True
    result = "".join(output).strip("-")
    if not result or result in {".", ".."} or len(result) > maximum:
        raise DocumentError("slot_invalid", f"{field} is empty, reserved, or too long", {"field": field})
    return result


def canonical_scope(value: Any) -> str:
    if not isinstance(value, str):
        raise DocumentError("scope_invalid", "scope must be a string")
    stripped = normalized_key(value.strip()).strip("/")
    if not stripped or "//" in stripped:
        raise DocumentError("scope_invalid", "scope is empty or contains an empty segment")
    raw_parts = stripped.split("/")
    if len(raw_parts) > 8:
        raise DocumentError("scope_invalid", "scope has more than eight segments")
    parts = [_canonical_slug_part(part, field="scope segment", maximum=40) for part in raw_parts]
    result = "/".join(parts)
    if len(result) > 160:
        raise DocumentError("scope_invalid", "scope exceeds 160 codepoints")
    return result


def canonical_document_key(value: Any) -> str:
    if not isinstance(value, str) or "/" in value:
        raise DocumentError("document_key_invalid", "document_key must be one project-local slot")
    return _canonical_slug_part(value, field="document_key", maximum=80)


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return nfc(value)
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**53 - 1:
        return value
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise DocumentError("canonical_json_invalid", "object keys must be strings")
            key = nfc(raw_key)
            if key in output:
                raise DocumentError("canonical_json_invalid", "NFC-normalized keys collide", {"key": key})
            output[key] = _canonical_value(raw_value)
        return {key: output[key] for key in sorted(output)}
    raise DocumentError("canonical_json_invalid", "unsupported canonical JSON scalar", {"type": type(value).__name__})


def canonical_json(value: Any) -> str:
    return json.dumps(_canonical_value(value), ensure_ascii=False, separators=(",", ":"))


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _serialize_public(value: Any) -> str:
    text = canonical_json(value) + "\n"
    size = len(text.encode("utf-8"))
    if size > MAX_PUBLIC_OUTPUT_BYTES:
        raise DocumentError(
            "output_too_large",
            "public output exceeds the 32 KiB UTF-8 byte budget",
            {"maximum": MAX_PUBLIC_OUTPUT_BYTES, "actual": size},
            EXIT_CONFLICT,
        )
    return text


def bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _version_parts(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise DocumentError("core_surface_mismatch", "context-core version is missing", exit_code=EXIT_CONFLICT)
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value)
    if match is None:
        raise DocumentError("core_surface_mismatch", "context-core version is invalid", exit_code=EXIT_CONFLICT)
    return tuple(int(part) for part in match.groups())


def core_surface_version(resolved: pathlib.Path) -> str:
    suffix = pathlib.PurePosixPath(REQUIRED_PLUGIN["entrypoint"]).parts
    plugin_root = resolved.parents[len(suffix) - 1]
    versions: set[str] = set()
    for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        try:
            manifest = json.loads((plugin_root / relative).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DocumentError("core_surface_mismatch", "context-core host manifests are unavailable or invalid", {"manifest": relative}, EXIT_CONFLICT) from error
        version = manifest.get("version") if isinstance(manifest, dict) else None
        if not isinstance(manifest, dict) or manifest.get("name") != "bobbin":
            raise DocumentError("core_surface_mismatch", "context-core manifest identity is invalid", exit_code=EXIT_CONFLICT)
        major, _, _ = _version_parts(version)
        if major != CORE_COMPATIBLE_MAJOR:
            raise DocumentError("core_surface_mismatch", "context-core major version is incompatible", {"required_major": CORE_COMPATIBLE_MAJOR, "observed_version": version}, EXIT_CONFLICT)
        versions.add(version)
    if len(versions) != 1:
        raise DocumentError("core_surface_mismatch", "context-core host manifests use different versions", exit_code=EXIT_CONFLICT)
    return versions.pop()


def _byte_size_details(actual: int, maximum: int) -> dict[str, int]:
    return {
        "actual_bytes": actual,
        "maximum_bytes": maximum,
        "over_by_bytes": max(0, actual - maximum),
    }


def _codepoint_size_details(actual: int, maximum: int) -> dict[str, int]:
    return {
        "actual_codepoints": actual,
        "maximum_codepoints": maximum,
        "over_by_codepoints": max(0, actual - maximum),
    }


def required_core_surface(value: str, *, expected_sha256: str | None = None) -> pathlib.Path:
    supplied = pathlib.Path(value)
    try:
        resolved = supplied.resolve(strict=True)
        digest = bytes_digest(resolved.read_bytes())
    except (OSError, RuntimeError) as error:
        raise DocumentError(
            "core_surface_unavailable",
            "the compatible context-core public CLI is unavailable; choose a listed candidate and start a new session",
            _core_failure_details(value, required_plugin=dict(REQUIRED_PLUGIN)),
            EXIT_CONFLICT,
        ) from error
    suffix = pathlib.PurePosixPath(REQUIRED_PLUGIN["entrypoint"]).parts
    if (
        not supplied.is_absolute()
        or not resolved.is_file()
        or tuple(resolved.parts[-len(suffix):]) != suffix
    ):
        raise DocumentError(
            "core_surface_mismatch",
            "context-core entrypoint path differs from the public compatibility contract; choose a listed candidate and start a new session",
            _core_failure_details(
                value,
                required_entrypoint=REQUIRED_PLUGIN["entrypoint"],
                observed_path=str(resolved),
                observed_sha256=digest,
            ),
            EXIT_CONFLICT,
        )
    try:
        core_surface_version(resolved)
    except DocumentError as error:
        if error.code == "core_surface_mismatch":
            error.message += "; choose a listed candidate and start a new session"
            error.details = _core_failure_details(value, **error.details)
        raise
    if expected_sha256 is not None and digest != expected_sha256:
        raise DocumentError("core_surface_changed", "context-core entrypoint changed during the operation", {"observed_sha256": digest}, EXIT_CONFLICT)
    return resolved


def validate_core_schema_handshake(value: Any, *, core_cli_value: str | None = None) -> dict[str, Any]:
    required_commands = {"doctor", "bootstrap", "transaction preview", "transaction apply"}
    features = value.get("features") if isinstance(value, dict) else None
    commands = value.get("commands") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema") != "context-core-schema/v1"
        or value.get("protocol") != PROTOCOL
        or not isinstance(features, list)
        or REQUIRED_FEATURE not in features
        or "filesystem-vault/v1" not in features
        or not isinstance(commands, list)
        or not required_commands.issubset(commands)
    ):
        raise DocumentError(
            "core_incompatible",
            "context-core schema, protocol, feature, or required command handshake is incompatible; choose a listed candidate and start a new session",
            _core_failure_details(core_cli_value, required_plugin=dict(REQUIRED_PLUGIN), required_commands=sorted(required_commands)),
            EXIT_CONFLICT,
        )
    return value


def file_bytes(content: str) -> bytes:
    return (content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n").encode("utf-8")


def file_digest(content: str) -> str:
    return bytes_digest(file_bytes(content))


def _substantive(value: Any, *, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise DocumentError("schema_invalid", f"{field} must be a string")
    result = nfc(value.strip())
    if not result or result in PLACEHOLDERS:
        raise DocumentError("schema_invalid", f"{field} is empty or a placeholder")
    if len(result) > maximum:
        raise DocumentError(
            "schema_invalid",
            f"{field} exceeds its {maximum}-codepoint limit",
            {"field": field, **_codepoint_size_details(len(result), maximum)},
            EXIT_CONFLICT,
        )
    return result


def _string_list(value: Any, field: str, *, minimum: int = 0, maximum: int = 12, item_maximum: int = 500) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise DocumentError("schema_invalid", f"{field} list bounds are invalid")
    result = [_substantive(item, maximum=item_maximum, field=field) for item in value]
    if len(result) != len(set(result)):
        raise DocumentError("schema_invalid", f"{field} contains duplicates")
    return result


def _valid_context_id(value: Any) -> bool:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        return False
    parsed = uuid.UUID(hex=value[4:])
    return parsed.version == 4 and parsed.variant == uuid.RFC_4122


def require_context_id(value: Any, field: str = "id") -> str:
    if not _valid_context_id(value):
        raise DocumentError("id_invalid", f"{field} must be ctx_ plus lowercase UUIDv4 hex", {"field": field})
    return value


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise DocumentError("schema_invalid", f"{field} must be a timestamp")
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as error:
        raise DocumentError("schema_invalid", f"{field} must be RFC3339-compatible") from error
    if parsed.tzinfo is None or parsed.isoformat(timespec="seconds") != value:
        raise DocumentError("schema_invalid", f"{field} must include an offset and seconds precision")
    return value


def now_rfc3339() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def new_context_id() -> str:
    return "ctx_" + uuid.uuid4().hex


def natural_filename(title: str) -> str:
    output: list[str] = []
    separator = False
    for char in nfc(title.strip()):
        if char.isalnum() or char in "-_.":
            output.append(char)
            separator = False
        elif not separator:
            output.append("-")
            separator = True
    stem = "".join(output).strip("-._")
    filename = stem + ".md"
    if not stem or len(filename) > 120 or len(filename.encode("utf-8")) > 240 or filename.endswith(".index.md"):
        raise DocumentError("filename_invalid", "title cannot produce a safe document filename")
    return filename



def owner_descriptor() -> dict[str, Any]:
    return {
        "schema": "context-owner-descriptor/v2",
        "owner": "context-document",
        "kind": "document",
        "artifact_schema": "context-document/v1",
        "authority": "authoritative",
        "structural_profile": {
            "schema": "context-structural-profile/v1",
            "fields": {
                "scope": {"type": "string", "required": True, "min_chars": 1, "max_chars": 160},
                "document_key": {"type": "string", "required": True, "min_chars": 1, "max_chars": 80},
            },
            "sections": {"ordered": ["Content"], "required": ["Content"], "primary": "Content"},
            "index_projection": ["scope", "document_key"],
            "lifecycle": {"allowed_topologies": ["create_current", "replace_same_state"], "reasons": {}},
        },
    }


def document_capability() -> dict[str, Any]:
    descriptor = owner_descriptor()
    return {
        "schema": "context-owner-capability/v1",
        "owner": "context-document",
        "kind": "document",
        "artifact_schema": "context-document/v1",
        "authority": "authoritative",
        "descriptor_digest": canonical_digest(descriptor),
        "claim_surface": {"type": "agent_skill", "name": "bobbin:document", "operation": "claim"},
        "claim_rule": "A current-state statement consumed by an agent or person through recall/envelopes has a stable authoritative slot and substantive content; external deliverable documents remain repository-owned and out of scope",
        "claim_assertions": ["content_present", "living_document"],
        "lifecycle_operations": {},
        "draft_fields": {
            "required": {
                "document_key": {"type": "string", "min_chars": 1, "max_chars": 80},
                "content": {"type": "string", "min_chars": 1, "max_chars": 6000},
            },
            "optional": {},
        },
    }


def schema_result() -> dict[str, Any]:
    return {
        "schema": "context-document-schema/v1",
        "protocol": PROTOCOL,
        "artifact_schema": "context-document/v1",
        "authority": "authoritative",
        "owner_descriptor": owner_descriptor(),
        "features": [REQUIRED_FEATURE, "exact-rfc6901-claim-binding", "scope-document-key-slot", "stable-id-update"],
        "workflow_surface": {
            "entrypoint": "document_workflow.py",
            "commands": ["preview", "apply"],
            "preview_input_modes": ["inline"],
            "preflight": "derived_from_verified_core_manifests_and_doctor",
            "preview_state": "awaiting_approval",
        },
        "physical_write": False,
    }


def render_document(frontmatter: dict[str, Any], sections: dict[str, str]) -> str:
    required = {"schema", "id", "title", "summary", "created_at", "captured_from", "scope", "document_key"}
    optional = {"tags", "search_terms", "source_refs", "updated_at"}
    if required - set(frontmatter) or set(frontmatter) - required - optional:
        raise DocumentError("schema_invalid", "document frontmatter fields are incomplete or unknown")
    if frontmatter.get("schema") != "context-document/v1":
        raise DocumentError("schema_invalid", "artifact schema must be context-document/v1")
    require_context_id(frontmatter.get("id"))
    for name, maximum in (("title", 120), ("summary", 280)):
        value = _substantive(frontmatter.get(name), maximum=maximum, field=name)
        if "\n" in value:
            raise DocumentError("schema_invalid", f"{name} must be one line")
    scope = canonical_scope(frontmatter.get("scope"))
    key = canonical_document_key(frontmatter.get("document_key"))
    if scope != frontmatter["scope"] or key != frontmatter["document_key"]:
        raise DocumentError("slot_invalid", "stored scope and document_key must already be canonical", exit_code=EXIT_CONFLICT)
    created_at = _timestamp(frontmatter.get("created_at"), "created_at")
    if "updated_at" in frontmatter:
        updated_at = _timestamp(frontmatter["updated_at"], "updated_at")
        if datetime.datetime.fromisoformat(updated_at) < datetime.datetime.fromisoformat(created_at):
            raise DocumentError("clock_invalid", "updated_at cannot precede created_at", exit_code=EXIT_CONFLICT)
    if frontmatter.get("captured_from") not in {"conversation", "workspace", "manual", "import"}:
        raise DocumentError("schema_invalid", "captured_from is invalid")
    for field in ("tags", "search_terms"):
        if field in frontmatter:
            _string_list(frontmatter[field], field, maximum=12, item_maximum=40)
    if "source_refs" in frontmatter:
        _string_list(frontmatter["source_refs"], "source_refs", maximum=12, item_maximum=500)
    if set(sections) != {"Content"}:
        raise DocumentError("schema_invalid", "document requires only the Content H2 section")
    _substantive(sections["Content"], maximum=6000, field="Content")
    lines = ["---"]
    for key_name, value in frontmatter.items():
        lines.append(f"{key_name}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}")
    lines.extend(["---", "", "## Content", "", sections["Content"], ""])
    return "\n".join(lines).rstrip("\n") + "\n"


def parse_document(text: str) -> tuple[dict[str, Any], dict[str, str]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0] != "---":
        raise DocumentError("schema_invalid", "artifact frontmatter is missing", exit_code=EXIT_INTEGRITY)
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise DocumentError("schema_invalid", "artifact frontmatter is unterminated", exit_code=EXIT_INTEGRITY) from error
    frontmatter: dict[str, Any] = {}
    for line in lines[1:closing]:
        if ": " not in line:
            raise DocumentError("schema_invalid", "artifact frontmatter is malformed", exit_code=EXIT_INTEGRITY)
        key, raw = line.split(": ", 1)
        if key in frontmatter:
            raise DocumentError("schema_invalid", "artifact frontmatter key is duplicated", {"field": key}, EXIT_INTEGRITY)
        try:
            frontmatter[key] = json.loads(raw)
        except json.JSONDecodeError as error:
            raise DocumentError("schema_invalid", "artifact frontmatter JSON is malformed", {"field": key}, EXIT_INTEGRITY) from error
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in lines[closing + 1:]:
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = line[3:]
            if current in sections:
                raise DocumentError("schema_invalid", "artifact section is duplicated", {"section": current}, EXIT_INTEGRITY)
            buffer = []
        elif current is not None:
            buffer.append(line)
        elif line.strip():
            raise DocumentError("schema_invalid", "content exists outside an H2 section", exit_code=EXIT_INTEGRITY)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    canonical = render_document(frontmatter, sections)
    if file_bytes(canonical) != file_bytes(text):
        raise DocumentError("schema_invalid", "artifact is not canonical", exit_code=EXIT_INTEGRITY)
    return frontmatter, sections


def validate_document_candidate(candidate: Any) -> tuple[dict[str, str], dict[str, str]]:
    candidate = validate_transport_candidate(candidate)
    boundary = _foreign_semantic_boundary(candidate)
    if boundary is not None:
        raise DocumentError("owner_decline", f"candidate belongs to the {boundary} semantic boundary", {"kind": boundary}, EXIT_CONFLICT)
    if candidate.get("requested_kind") != "document" and "document" not in candidate.get("specialized_kinds", []):
        raise DocumentError("owner_decline", "candidate is not offered to the document owner", exit_code=EXIT_CONFLICT)
    owner_input = candidate.get("owner_inputs", {}).get("document")
    if not isinstance(owner_input, dict) or set(owner_input) != {"document_key", "content"}:
        raise DocumentError("candidate_invalid", "document owner input fields are incomplete or unknown", exit_code=EXIT_CONFLICT)
    content = _substantive(owner_input["content"], maximum=6000, field="content")
    if candidate["claim"] != content:
        raise DocumentError("candidate_invalid", "candidate claim must equal Content", exit_code=EXIT_CONFLICT)
    normalized = {"content": content, "document_key": canonical_document_key(owner_input["document_key"])}
    return normalized, {"scope": canonical_scope(candidate.get("scope_hint")), "document_key": normalized["document_key"]}


def build_claim_result(
    candidate: dict[str, Any],
    attestation: dict[str, Any],
    *,
    identifier: str | None = None,
    created_at: str | None = None,
    filename: str | None = None,
    route_only: bool = False,
) -> dict[str, Any]:
    validate_transport_candidate(candidate)
    boundary = _foreign_semantic_boundary(candidate)
    if boundary is not None:
        return build_decline_result(candidate, f"{boundary} semantic boundary is not a document")
    owner_input, structural = validate_document_candidate(candidate)
    validate_attestation(attestation, candidate, "claim", {
        "content_present": ("/owner_inputs/document/content",),
        "living_document": ("/owner_inputs/document/document_key", "/owner_inputs/document/content"),
    })
    drafts: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    if not route_only:
        identifier = require_context_id(identifier or new_context_id())
        created_at = _timestamp(created_at or now_rfc3339(), "created_at")
        frontmatter: dict[str, Any] = {
            "schema": "context-document/v1", "id": identifier, "title": candidate["title"],
            "summary": candidate["summary"], "created_at": created_at, "captured_from": candidate["captured_from"],
            "scope": structural["scope"], "document_key": structural["document_key"],
        }
        for field in ("tags", "search_terms", "source_refs"):
            if candidate.get(field):
                frontmatter[field] = list(candidate[field])
        content = render_document(frontmatter, {"Content": owner_input["content"]})
        path = "context/document/" + (filename or natural_filename(candidate["title"]))
        effect_id = "effect_create_document"
        drafts = [{
            "effect_id": effect_id, "path": path, "content": content,
            "semantic_projection": {"kind": "document", "primary_claim": owner_input["content"], "supporting_context": []},
        }]
        effects = [{"effect_id": effect_id, "action": "create", "area": "document", "id": identifier, "state": "current"}]
        operations = [{"op": "create", "effect_id": effect_id, "area": "document", "path": path}]
    result = {
        "schema": "context-owner-result/v1", "result_type": "claim", "transition": "capture",
        "owner": "context-document", "target_kind": "document", "candidate_id": candidate["candidate_id"],
        "decision": "claim", "reason": "authoritative living document", "capability_digest": canonical_digest(document_capability()),
        "semantic_inputs": [_semantic_input("claim", candidate)], "semantic_attestations": [attestation],
        "artifact_drafts": drafts, "effects": effects,
        "proposed_plan": {"schema": "context-owner-plan/v1", "transition": "capture", "operations": operations},
    }
    validate_owner_result(result)
    return result


def build_decline_result(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    candidate = validate_transport_candidate(candidate)
    return {
        "schema": "context-owner-result/v1", "result_type": "claim", "transition": "capture",
        "owner": "context-document", "target_kind": "document", "candidate_id": candidate["candidate_id"],
        "decision": "decline", "reason": _substantive(reason, maximum=500, field="reason"),
        "capability_digest": canonical_digest(document_capability()), "semantic_inputs": [_semantic_input("claim", candidate)],
        "semantic_attestations": [], "artifact_drafts": [], "effects": [], "proposed_plan": None,
    }


def _validate_projection(draft: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    frontmatter, sections = parse_document(draft.get("content", ""))
    projection = draft.get("semantic_projection")
    if projection != {"kind": "document", "primary_claim": sections["Content"], "supporting_context": []}:
        raise DocumentError("owner_result_invalid", "semantic projection differs from actual Content", exit_code=EXIT_CONFLICT)
    return frontmatter, sections


def validate_owner_result(result: Any) -> None:
    required = {
        "schema", "result_type", "transition", "owner", "target_kind", "capability_digest", "semantic_inputs",
        "semantic_attestations", "artifact_drafts", "effects", "proposed_plan",
    }
    if not isinstance(result, dict) or result.get("schema") != "context-owner-result/v1" or required - set(result):
        raise DocumentError("owner_result_invalid", "owner result envelope is incomplete", exit_code=EXIT_CONFLICT)
    if result.get("owner") != "context-document" or result.get("target_kind") != "document" or result.get("capability_digest") != canonical_digest(document_capability()):
        raise DocumentError("capability_digest_mismatch", "owner result is not bound to the DOCUMENT capability", exit_code=EXIT_CONFLICT)
    raw_inputs, raw_attestations = result.get("semantic_inputs"), result.get("semantic_attestations")
    if not isinstance(raw_inputs, list) or not isinstance(raw_attestations, list):
        raise DocumentError("owner_result_invalid", "semantic evidence collections must be lists", exit_code=EXIT_CONFLICT)
    inputs: dict[str, dict[str, Any]] = {}
    for item in raw_inputs:
        if not isinstance(item, dict) or not isinstance(item.get("value"), dict):
            raise DocumentError("semantic_input_invalid", "semantic input must be an exact object", exit_code=EXIT_CONFLICT)
        operation = item.get("operation")
        if operation in inputs or item.get("input_schema") != item["value"].get("schema") or item.get("input_digest") != canonical_digest(item["value"]):
            raise DocumentError("semantic_input_invalid", "semantic input binding is invalid", exit_code=EXIT_CONFLICT)
        inputs[operation] = item
    if any(not isinstance(item, dict) for item in raw_attestations):
        raise DocumentError("semantic_attestation_invalid", "semantic attestation must be an object", exit_code=EXIT_CONFLICT)
    attestations = {item.get("operation"): item for item in raw_attestations}
    if len(attestations) != len(raw_attestations):
        raise DocumentError("semantic_attestation_invalid", "semantic attestation is duplicated", exit_code=EXIT_CONFLICT)
    if result["result_type"] == "claim" and result.get("decision") == "decline":
        if set(inputs) != {"claim"} or attestations or result.get("artifact_drafts") or result.get("effects") or result.get("proposed_plan") is not None:
            raise DocumentError("owner_result_invalid", "decline must bind only the exact candidate", exit_code=EXIT_CONFLICT)
        return
    if result["result_type"] == "claim":
        if result.get("decision") != "claim" or result.get("transition") != "capture" or set(inputs) != {"claim"} or set(attestations) != {"claim"}:
            raise DocumentError("owner_result_invalid", "claim evidence is incomplete", exit_code=EXIT_CONFLICT)
        validate_document_candidate(inputs["claim"]["value"])
        validate_attestation(attestations["claim"], inputs["claim"]["value"], "claim", {
            "content_present": ("/owner_inputs/document/content",),
            "living_document": ("/owner_inputs/document/document_key", "/owner_inputs/document/content"),
        })
    elif result["result_type"] == "mutation":
        if result.get("transition") != "document_update" or set(inputs) != {"mutation_request"} or attestations:
            raise DocumentError("owner_result_invalid", "update evidence is incomplete", exit_code=EXIT_CONFLICT)
    else:
        raise DocumentError("owner_result_invalid", "owner result type is unsupported", exit_code=EXIT_CONFLICT)
    plan = result.get("proposed_plan")
    drafts, effects = result.get("artifact_drafts"), result.get("effects")
    operations = plan.get("operations") if isinstance(plan, dict) else None
    if not isinstance(plan, dict) or plan.get("schema") != "context-owner-plan/v1" or plan.get("transition") != result["transition"] or not all(isinstance(value, list) for value in (drafts, effects, operations)):
        raise DocumentError("owner_result_invalid", "owner plan is invalid", exit_code=EXIT_CONFLICT)
    for collection in (drafts, effects, operations):
        ids = [item.get("effect_id") for item in collection]
        if any(not isinstance(item, str) or not LOCAL_ID_RE.fullmatch(item) for item in ids) or len(ids) != len(set(ids)):
            raise DocumentError("plan_preview_mismatch", "effect ids are invalid", exit_code=EXIT_CONFLICT)
    if {item["effect_id"] for item in effects} != {item["effect_id"] for item in operations}:
        raise DocumentError("plan_preview_mismatch", "effects and operations are not 1:1", exit_code=EXIT_CONFLICT)
    draft_map = {draft["effect_id"]: _validate_projection(draft) for draft in drafts}
    if set(draft_map) != {item["effect_id"] for item in operations} or any(item.get("area") != "document" for item in operations):
        raise DocumentError("plan_preview_mismatch", "operation escapes the document area", exit_code=EXIT_CONFLICT)
    if result["transition"] == "capture":
        if len(drafts) not in {0, 1} or any(item.get("op") != "create" for item in operations):
            raise DocumentError("plan_preview_mismatch", "capture creates at most one document", exit_code=EXIT_CONFLICT)
    else:
        if len(drafts) != 1 or len(effects) != 1 or len(operations) != 1:
            raise DocumentError("plan_preview_mismatch", "update must replace one document", exit_code=EXIT_CONFLICT)
        draft = drafts[0]
        frontmatter, _ = draft_map[draft["effect_id"]]
        effect, operation = effects[0], operations[0]
        if (
            effect != {"effect_id": draft["effect_id"], "action": "replace", "area": "document", "id": frontmatter["id"], "state": "current"}
            or operation.get("op") != "replace"
            or operation.get("path") != draft["path"]
            or operation.get("id") != frontmatter["id"]
        ):
            raise DocumentError("stable_id_update_invalid", "update must retain one current id and path", exit_code=EXIT_CONFLICT)


def document_index_seed() -> str:
    descriptor = canonical_json(owner_descriptor())
    return f"""---
schema: "context-area-index/v1"
index: true
area: "document"
owner: "context-document"
artifact_schema: "context-document/v1"
authority: "authoritative"
summary: "Project-scoped living documents with stable authoritative slots."
search_terms: ["document","living document","content"]
projection_fields: ["scope","document_key"]
---

<!-- BEGIN CONTEXT GENERATED:owner-profile -->
{descriptor}
<!-- END CONTEXT GENERATED:owner-profile -->

# Document

## Current
<!-- BEGIN CONTEXT GENERATED:current -->
<!-- END CONTEXT GENERATED:current -->

## History
<!-- BEGIN CONTEXT GENERATED:history -->
<!-- END CONTEXT GENERATED:history -->
"""


def build_init_plan(preflight: dict[str, Any] | None = None) -> dict[str, Any]:
    descriptor = owner_descriptor()
    seed = document_index_seed()
    return {
        "schema": "context-document-init-plan/v1", "required_plugin": dict(REQUIRED_PLUGIN), "required_feature": REQUIRED_FEATURE,
        "core_repository_state": "ready" if preflight is None else preflight["observed"]["repository_state"],
        "active_core_entrypoint": None if preflight is None else preflight["observed"].get("entrypoint"),
        "owner_descriptor": descriptor, "descriptor_digest": canonical_digest(descriptor), "index_seed": seed,
        "index_seed_sha256": file_digest(seed),
        "bootstrap": {"owner": "context-core", "operation": "bootstrap", "host": "active_host" if preflight is None else preflight.get("host", "active_host"), "area_register": "context-document", "index_path": DOCUMENT_INDEX},
        "applied": False,
    }


def _safe_document_path(repo: pathlib.Path, relative: str, *, index: bool = False) -> pathlib.Path:
    if not isinstance(relative, str):
        raise DocumentError("path_escape", "document path must be a string", exit_code=EXIT_CONFLICT)
    pure = pathlib.PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise DocumentError("path_escape", "path must be canonical vault-relative POSIX", {"path": relative}, EXIT_CONFLICT)
    valid_shape = relative == DOCUMENT_INDEX if index else (
        len(pure.parts) == 3
        and pure.parts[:2] == ("context", "document")
        and pure.suffix == ".md"
        and not pure.name.endswith(".index.md")
    )
    if not valid_shape:
        raise DocumentError("path_escape", "path escapes the exact document artifact layout", {"path": relative}, EXIT_CONFLICT)
    root = repo.resolve()
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise DocumentError("symlink_path", "symlink path components are not readable DOCUMENT artifacts", {"path": relative}, EXIT_CONFLICT)
    target = root.joinpath(*pure.parts)
    try:
        target.resolve(strict=False).relative_to((root / "context" / "document").resolve(strict=False))
    except ValueError as error:
        raise DocumentError("path_escape", "resolved path escapes context/document", {"path": relative}, EXIT_CONFLICT) from error
    return target


def parse_document_index(text: str) -> list[dict[str, Any]]:
    if 'area: "document"' not in text or 'owner: "context-document"' not in text or canonical_json(owner_descriptor()) not in _extract_block(text, "owner-profile"):
        raise DocumentError("index_stale", "document index descriptor/profile is invalid", exit_code=EXIT_INTEGRITY)
    rows: list[dict[str, Any]] = []
    for line in _extract_block(text, "current"):
        match = ENTRY_RE.fullmatch(line)
        if not match:
            raise DocumentError("index_stale", "document index row is malformed", exit_code=EXIT_INTEGRITY)
        try:
            row = json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise DocumentError("index_stale", "document index row JSON is malformed", exit_code=EXIT_INTEGRITY) from error
        if row.get("state") != "current" or not _valid_context_id(row.get("id")) or not isinstance(row.get("path"), str):
            raise DocumentError("index_stale", "document index row identity/state is invalid", exit_code=EXIT_INTEGRITY)
        rows.append(row)
    if _extract_block(text, "history"):
        raise DocumentError("index_stale", "document owner does not support historical rows", exit_code=EXIT_INTEGRITY)
    return rows


def _index(repo: pathlib.Path) -> tuple[str, list[dict[str, Any]]]:
    path = _safe_document_path(repo, DOCUMENT_INDEX, index=True)
    if not path.is_file():
        raise DocumentError("document_area_missing", "document area index is missing", {"path": DOCUMENT_INDEX}, EXIT_NOT_FOUND)
    text = path.read_text(encoding="utf-8")
    current = parse_document_index(text)
    for row in current:
        _safe_document_path(repo, row["path"])
    return text, current


def _record(repo: pathlib.Path, row: dict[str, Any]) -> dict[str, Any]:
    path = _safe_document_path(repo, row.get("path"))
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise DocumentError("index_stale", "selected document path is missing", {"path": row["path"]}, EXIT_INTEGRITY) from error
    frontmatter, sections = parse_document(raw.decode("utf-8"))
    if frontmatter["id"] != row["id"]:
        raise DocumentError("index_stale", "selected document id differs from index", {"path": row["path"]}, EXIT_INTEGRITY)
    return {"path": row["path"], "state": "current", "frontmatter": frontmatter, "sections": sections, "sha256": bytes_digest(raw)}


def _current_record(repo: pathlib.Path, identifier: str) -> dict[str, Any]:
    require_context_id(identifier)
    _, current = _index(repo)
    matches = [row for row in current if row["id"] == identifier]
    if len(matches) != 1:
        raise DocumentError("artifact_not_found", "Current document id was not found exactly once", {"id": identifier}, EXIT_NOT_FOUND)
    return _record(repo, matches[0])


def _mutation_request(record: dict[str, Any], content: str) -> dict[str, Any]:
    return {
        "schema": "context-domain-mutation-input/v1",
        "transition": "document_update",
        "owner": "context-document",
        "target_kind": "document",
        "requested_changes": {"content": content},
        "targets": [{"id": record["frontmatter"]["id"], "path": record["path"], "sha256": record["sha256"]}],
    }


def build_update_result(repo: pathlib.Path, identifier: str, content: str, *, updated_at: str | None = None) -> dict[str, Any]:
    record = _current_record(repo, identifier)
    content = _substantive(content, maximum=6000, field="content")
    if content == record["sections"]["Content"]:
        raise DocumentError("no_change", "update Content must differ from Current", exit_code=EXIT_CONFLICT)
    at = _timestamp(updated_at or now_rfc3339(), "updated_at")
    if datetime.datetime.fromisoformat(at) < datetime.datetime.fromisoformat(record["frontmatter"]["created_at"]):
        raise DocumentError("clock_invalid", "updated_at cannot precede created_at", exit_code=EXIT_CONFLICT)
    frontmatter = dict(record["frontmatter"])
    frontmatter["updated_at"] = at
    effect_id = "effect_replace_document"
    request = _mutation_request(record, content)
    result = {
        "schema": "context-owner-result/v1", "result_type": "mutation", "transition": "document_update",
        "owner": "context-document", "target_kind": "document", "capability_digest": canonical_digest(document_capability()),
        "semantic_inputs": [_semantic_input("mutation_request", request)], "semantic_attestations": [],
        "artifact_drafts": [{
            "effect_id": effect_id, "path": record["path"], "content": render_document(frontmatter, {"Content": content}),
            "semantic_projection": {"kind": "document", "primary_claim": content, "supporting_context": []},
        }],
        "effects": [{"effect_id": effect_id, "action": "replace", "area": "document", "id": identifier, "state": "current"}],
        "proposed_plan": {
            "schema": "context-owner-plan/v1", "transition": "document_update",
            "read_preconditions": [{"id": identifier, "path": record["path"], "sha256": record["sha256"]}],
            "operations": [{"op": "replace", "effect_id": effect_id, "area": "document", "id": identifier, "path": record["path"]}],
        },
    }
    validate_owner_result(result)
    return result


def search_documents(repo: pathlib.Path, *, query: str = "", limit: int = 20) -> dict[str, Any]:
    if not 1 <= limit <= 50:
        raise DocumentError("usage_invalid", "search limit must be in 1..50")
    _, current = _index(repo)
    needle = normalized_key(query.strip())
    items = []
    for row in current:
        haystack = " ".join(str(row.get(field, "")) for field in ("title", "summary", "scope", "document_key"))
        if needle and needle not in normalized_key(haystack):
            continue
        item = {key: row.get(key) for key in ("id", "path", "title", "summary", "scope", "document_key", "state", "created_at", "updated_at") if key in row}
        item.update({"authority": "authoritative", "do_not_follow": False})
        items.append(item)
    items.sort(key=lambda item: (item.get("updated_at", item.get("created_at", "")), item["id"]), reverse=True)
    return {"schema": "context-document-search/v1", "items": items[:limit], "returned": min(len(items), limit), "metadata_only": True, "physical_write": False}


def read_document(repo: pathlib.Path, *, identifier: str) -> dict[str, Any]:
    record = _current_record(repo, identifier)
    return {"schema": "context-document-read/v1", "id": identifier, "path": record["path"], "state": "current", "authority": "authoritative", "do_not_follow": False, "frontmatter": record["frontmatter"], "sections": record["sections"], "sha256": record["sha256"], "physical_write": False}


def _input_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["operation"]: item for item in result["semantic_inputs"]}


def _rebuild_owner_result(repo: pathlib.Path, owner_result: dict[str, Any]) -> dict[str, Any]:
    validate_owner_result(owner_result)
    inputs, drafts = _input_map(owner_result), owner_result["artifact_drafts"]
    if owner_result["transition"] == "capture":
        if len(drafts) != 1:
            raise DocumentError("owner_result_invalid", "batch validation requires a complete capture draft", exit_code=EXIT_CONFLICT)
        frontmatter, _ = parse_document(drafts[0]["content"])
        attestation = owner_result["semantic_attestations"][0]
        expected = build_claim_result(inputs["claim"]["value"], attestation, identifier=frontmatter["id"], created_at=frontmatter["created_at"], filename=pathlib.PurePosixPath(drafts[0]["path"]).name)
    elif owner_result["transition"] == "document_update":
        if len(drafts) != 1:
            raise DocumentError("owner_result_invalid", "batch validation requires one update draft", exit_code=EXIT_CONFLICT)
        request = inputs["mutation_request"]["value"]
        targets = request.get("targets")
        changes = request.get("requested_changes")
        if not isinstance(targets, list) or len(targets) != 1 or not isinstance(changes, dict) or set(changes) != {"content"}:
            raise DocumentError("mutation_request_invalid", "update request is incomplete or unknown", exit_code=EXIT_CONFLICT)
        record = _current_record(repo, targets[0].get("id"))
        expected_target = {"id": record["frontmatter"]["id"], "path": record["path"], "sha256": record["sha256"]}
        if targets[0] != expected_target:
            raise DocumentError("source_precondition_mismatch", "update target differs from live Current artifact", exit_code=EXIT_CONFLICT)
        frontmatter, _ = parse_document(drafts[0]["content"])
        expected = build_update_result(repo, record["frontmatter"]["id"], changes["content"], updated_at=frontmatter.get("updated_at"))
    else:
        raise DocumentError("transition_topology_invalid", "unsupported DOCUMENT transition", exit_code=EXIT_CONFLICT)
    if expected != owner_result:
        raise DocumentError("owner_result_rederivation_mismatch", "owner result differs from live source or exact semantic inputs", exit_code=EXIT_CONFLICT)
    return expected


def _validate_prior_chain(prior_bundles: Sequence[dict[str, Any]]) -> list[str]:
    all_digests: list[str] = []
    for bundle in prior_bundles:
        plan = bundle.get("approval_material", {}).get("plan") if isinstance(bundle, dict) else None
        if not isinstance(bundle, dict) or bundle.get("schema") != "context-mutation-bundle/v1" or bundle.get("approval_digest") != canonical_digest(bundle.get("approval_material")) or not isinstance(plan, dict) or plan.get("prior_bundle_digests") != all_digests:
            raise DocumentError("prior_bundle_invalid", "prior bundle chain is invalid", exit_code=EXIT_CONFLICT)
        all_digests.append(bundle["approval_digest"])
        if plan.get("owner") == "context-document" or plan.get("primary_area") == "document":
            raise DocumentError("prior_same_area_requires_apply", "unapplied same-area DOCUMENT bundles are unsupported", exit_code=EXIT_CONFLICT)
    return []


def _validate_document_slots(repo: pathlib.Path, owner_result: dict[str, Any]) -> None:
    _, rows = _index(repo)
    current = [_record(repo, row)["frontmatter"] for row in rows]
    targets = {target["id"] for item in owner_result["semantic_inputs"] if item["operation"] == "mutation_request" for target in item["value"].get("targets", [])}
    current = [item for item in current if item["id"] not in targets]
    current.extend(parse_document(draft["content"])[0] for draft in owner_result["artifact_drafts"])
    slots: dict[tuple[str, str], str] = {}
    for item in current:
        slot = (item["scope"], item["document_key"])
        if slot in slots:
            raise DocumentError("document_slot_conflict", "Current documents share the same scope and document_key", {"left": slots[slot], "right": item["id"]}, EXIT_CONFLICT)
        slots[slot] = item["id"]


def validate_batch(repo: pathlib.Path, owner_result: dict[str, Any], prior_bundles: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    _rebuild_owner_result(repo, owner_result)
    _validate_document_slots(repo, owner_result)
    index_text, _ = _index(repo)
    same_area = _validate_prior_chain(prior_bundles)
    topology = {"capture": "create_current", "document_update": "replace_same_state"}.get(owner_result["transition"])
    if topology is None:
        raise DocumentError("transition_topology_invalid", "DOCUMENT transition has no generic topology", exit_code=EXIT_CONFLICT)
    receipt = {
        "schema": "context-owner-validation-receipt/v2", "owner": "context-document", "kind": "document",
        "descriptor_digest": canonical_digest(owner_descriptor()), "capability": document_capability(),
        "owner_result_digest": canonical_digest(owner_result), "base_area_index_sha256": bytes_digest(index_text.encode("utf-8")),
        "prior_same_area_bundle_digests": same_area, "transition_topology": topology,
        "semantic_input_digests": {item["operation"]: item["input_digest"] for item in owner_result["semantic_inputs"]}, "status": "valid",
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    return receipt


def _add_preflight(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", choices=("codex", "claude-code"))
    parser.add_argument("--core-inventory")
    parser.add_argument("--core-doctor")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="document_cli.py")
    parser.add_argument("--vault", help="Directory containing context/; defaults to the nearest context/ ancestor or cwd.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("schema", "capabilities"):
        sub.add_parser(name).add_argument("--json", action="store_true")
    init = sub.add_parser("init"); init.add_argument("--json", action="store_true")
    claim = sub.add_parser("claim"); claim.add_argument("--candidate", required=True); claim.add_argument("--attestation", required=True); claim.add_argument("--identifier"); claim.add_argument("--created-at"); claim.add_argument("--filename"); claim.add_argument("--route-only", action="store_true"); claim.add_argument("--json", action="store_true")
    decline = sub.add_parser("decline"); decline.add_argument("--candidate", required=True); decline.add_argument("--reason", required=True); decline.add_argument("--json", action="store_true")
    candidate_batch = sub.add_parser("candidate-batch"); candidate_sub = candidate_batch.add_subparsers(dest="candidate_batch_command", required=True); candidate_validate = candidate_sub.add_parser("validate"); candidate_validate.add_argument("--batch", required=True); candidate_validate.add_argument("--json", action="store_true")
    search = sub.add_parser("search"); search.add_argument("--query", default=""); search.add_argument("--limit", type=int, default=20); search.add_argument("--json", action="store_true")
    read = sub.add_parser("read"); read.add_argument("--id", required=True); read.add_argument("--json", action="store_true")
    update = sub.add_parser("update"); update.add_argument("--id", required=True); update.add_argument("--content", required=True); update.add_argument("--updated-at"); update.add_argument("--json", action="store_true")
    batch = sub.add_parser("batch"); batch_sub = batch.add_subparsers(dest="batch_command", required=True); validate = batch_sub.add_parser("validate"); validate.add_argument("--owner-result", required=True); validate.add_argument("--prior-bundle", action="append", default=[]); validate.add_argument("--json", action="store_true")
    for command in (init, claim, decline, candidate_validate, search, read, update, validate):
        _add_preflight(command)
    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "schema":
        return schema_result()
    if args.command == "capabilities":
        return {"schema": "context-owner-capabilities/v1", "owners": [document_capability()]}
    preflight = require_core_preflight(args, allow_absent=args.command == "init", allow_partial=args.command == "init")
    if args.command == "init":
        return build_init_plan(preflight)
    if args.command == "claim":
        return build_claim_result(_load_json_argument(args.candidate, allow_stdin=True), _load_json_argument(args.attestation), identifier=args.identifier, created_at=args.created_at, filename=args.filename, route_only=args.route_only)
    if args.command == "decline":
        return build_decline_result(_load_json_argument(args.candidate, allow_stdin=True), args.reason)
    if args.command == "candidate-batch":
        return validate_candidate_batch(_load_json_argument(args.batch, allow_stdin=True))
    repo = vault_root(getattr(args, "vault", None))
    if args.command == "search":
        return search_documents(repo, query=args.query, limit=args.limit)
    if args.command == "read":
        return read_document(repo, identifier=args.id)
    if args.command == "update":
        return build_update_result(repo, args.id, args.content, updated_at=args.updated_at)
    if args.command == "batch":
        return validate_batch(repo, _load_json_argument(args.owner_result, allow_stdin=True), [_load_json_argument(value) for value in args.prior_bundle])
    raise DocumentError("usage_invalid", "unsupported command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        with _bobbin_settings("project_environment"):
            result = dispatch(args)
        output = {"ok": True, "result": result} if getattr(args, "json", False) else result
        sys.stdout.write(_serialize_public(output))
        return 0
    except DocumentError as error:
        try:
            sys.stdout.write(_serialize_public(error.envelope()))
        except DocumentError:
            sys.stdout.write('{"error":{"code":"output_too_large","details":{"maximum":32768},"message":"public output exceeds the 32 KiB UTF-8 byte budget"},"ok":false}\n')
        return error.exit_code


def validate_transport_candidate(candidate: Any) -> dict[str, Any]:
    required = {
        "schema", "candidate_id", "title", "claim", "summary", "captured_from", "requested_kind",
        "specialized_kinds", "fallback_kind", "owner_inputs",
    }
    if (
        not isinstance(candidate, dict)
        or candidate.get("schema") != "context-capture-candidate/v1"
        or required - set(candidate)
        or set(candidate) - CANDIDATE_FIELDS
    ):
        raise DocumentError("candidate_invalid", "candidate envelope is incomplete", exit_code=EXIT_CONFLICT)
    candidate_bytes = len(canonical_json(candidate).encode("utf-8"))
    if candidate_bytes > MAX_CANDIDATE_BYTES:
        raise DocumentError(
            "candidate_too_large",
            "candidate exceeds the 16 KiB protocol budget",
            _byte_size_details(candidate_bytes, MAX_CANDIDATE_BYTES),
            EXIT_CONFLICT,
        )
    if not isinstance(candidate.get("candidate_id"), str) or re.fullmatch(r"cand_[0-9a-f]{32}", candidate["candidate_id"]) is None:
        raise DocumentError("candidate_invalid", "candidate_id is invalid")
    for name, maximum in (("title", 120), ("claim", MAX_PRIMARY_CLAIM_CODEPOINTS), ("summary", 280)):
        _substantive(candidate.get(name), maximum=maximum, field=name)
    if candidate.get("captured_from") not in {"conversation", "workspace", "manual", "import"}:
        raise DocumentError("candidate_invalid", "captured_from is invalid")
    specialized = candidate.get("specialized_kinds")
    if not isinstance(specialized, list) or len(specialized) > 2 or len(specialized) != len(set(specialized)) or not all(isinstance(item, str) for item in specialized):
        raise DocumentError("candidate_invalid", "specialized_kinds is invalid")
    if candidate.get("fallback_kind") not in {None, "observation", "snapshot"}:
        raise DocumentError("candidate_invalid", "fallback_kind is invalid")
    requested = candidate.get("requested_kind")
    if requested is not None and not isinstance(requested, str):
        raise DocumentError("candidate_invalid", "requested_kind must be a string or null")
    owner_inputs = candidate.get("owner_inputs")
    if not isinstance(owner_inputs, dict) or set(owner_inputs) - KNOWN_OWNER_KINDS:
        raise DocumentError("candidate_invalid", "owner_inputs must be an object")
    relevant = set(specialized) | ({requested} if requested else set()) | ({candidate["fallback_kind"]} if candidate["fallback_kind"] else set())
    foreign_owner_inputs = set(owner_inputs) - {"document"}
    if not foreign_owner_inputs and set(owner_inputs) - relevant:
        raise DocumentError("candidate_invalid", "owner_inputs contains a kind not offered by the candidate", exit_code=EXIT_CONFLICT)
    for kind, value in owner_inputs.items():
        if not isinstance(value, dict):
            raise DocumentError("candidate_invalid", "each owner input must be an object", {"kind": kind}, EXIT_CONFLICT)
        owner_input_bytes = len(canonical_json(value).encode("utf-8"))
        if owner_input_bytes > MAX_OWNER_INPUT_BYTES:
            raise DocumentError(
                "owner_input_too_large",
                "owner input exceeds the 8 KiB protocol budget",
                {"kind": kind, **_byte_size_details(owner_input_bytes, MAX_OWNER_INPUT_BYTES)},
                EXIT_CONFLICT,
            )
    for field, maximum, item_maximum in (("evidence", 2, 240), ("tags", 12, 40), ("search_terms", 12, 40), ("source_refs", 12, 500)):
        if field in candidate:
            _string_list(candidate[field], field, maximum=maximum, item_maximum=item_maximum)
    return candidate


def _foreign_semantic_boundary(candidate: dict[str, Any]) -> str | None:
    """Return the non-DOCUMENT semantic kind that makes this candidate a decline."""

    offered = set(candidate.get("specialized_kinds", []))
    if candidate.get("requested_kind"):
        offered.add(candidate["requested_kind"])
    offered.update(candidate.get("owner_inputs", {}))
    foreign = sorted(offered - {"document"})
    return foreign[0] if foreign else None


def validate_candidate_batch(batch: Any) -> dict[str, Any]:
    if (
        not isinstance(batch, dict)
        or set(batch) != {"schema", "audit_count", "candidates"}
        or batch.get("schema") != "context-capture-batch/v1"
        or batch.get("audit_count") != 1
        or not isinstance(batch.get("candidates"), list)
    ):
        raise DocumentError("candidate_invalid", "candidate batch envelope is invalid", exit_code=EXIT_CONFLICT)
    candidates = batch["candidates"]
    if len(candidates) > 8:
        raise DocumentError("candidate_batch_too_large", "candidate batch exceeds eight items", exit_code=EXIT_CONFLICT)
    batch_bytes = len(canonical_json(batch).encode("utf-8"))
    if batch_bytes > MAX_CANDIDATE_BYTES:
        raise DocumentError(
            "candidate_batch_too_large",
            "candidate batch exceeds the 16 KiB protocol budget",
            _byte_size_details(batch_bytes, MAX_CANDIDATE_BYTES),
            EXIT_CONFLICT,
        )
    for candidate in candidates:
        validate_transport_candidate(candidate)
    identifiers = [candidate["candidate_id"] for candidate in candidates]
    if len(identifiers) != len(set(identifiers)):
        raise DocumentError("candidate_invalid", "candidate batch contains duplicate candidate_id", exit_code=EXIT_CONFLICT)
    return {
        "schema": "context-document-candidate-batch-validation/v1",
        "status": "valid",
        "count": len(candidates),
        "canonical_bytes": batch_bytes,
        "physical_write": False,
    }


def _resolve_pointer(value: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise DocumentError("semantic_attestation_invalid", "evidence pointer must be RFC 6901", exit_code=EXIT_CONFLICT)
    current = value
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if "~" in token:
            raise DocumentError("semantic_attestation_invalid", "evidence pointer escape is invalid", exit_code=EXIT_CONFLICT)
        try:
            current = current[int(token)] if isinstance(current, list) else current[token]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise DocumentError("semantic_attestation_invalid", "evidence pointer does not resolve", {"pointer": pointer}, EXIT_CONFLICT) from error
    if current in (None, "", [], {}, False):
        raise DocumentError("semantic_attestation_invalid", "evidence pointer resolves to an empty value", {"pointer": pointer}, EXIT_CONFLICT)
    return current


def validate_attestation(attestation: Any, value: dict[str, Any], operation: str, assertions: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    envelope_fields = {"schema", "operation", "input_schema", "input_digest", "assertions"}
    if (
        not isinstance(attestation, dict)
        or set(attestation) != envelope_fields
        or attestation.get("schema") != "context-semantic-attestation/v1"
    ):
        raise DocumentError("semantic_attestation_invalid", "attestation envelope is invalid", exit_code=EXIT_CONFLICT)
    if (
        not isinstance(value, dict)
        or attestation.get("operation") != operation
        or attestation.get("input_schema") != value.get("schema")
        or attestation.get("input_digest") != canonical_digest(value)
    ):
        raise DocumentError("semantic_attestation_invalid", "attestation is not bound to the exact semantic input", exit_code=EXIT_CONFLICT)
    items = attestation.get("assertions")
    assertion_fields = {"name", "value", "evidence_pointers"}
    if (
        not isinstance(items, list)
        or len(items) != len(assertions)
        or any(not isinstance(item, dict) or set(item) != assertion_fields for item in items)
    ):
        raise DocumentError("semantic_attestation_invalid", "attestation assertions are not exact objects", exit_code=EXIT_CONFLICT)
    names = [item["name"] for item in items]
    if any(not isinstance(name, str) for name in names) or len(names) != len(set(names)) or set(names) != set(assertions):
        raise DocumentError("semantic_attestation_invalid", "attestation assertions differ from the owner contract", exit_code=EXIT_CONFLICT)
    for item in items:
        name = item["name"]
        pointers = item["evidence_pointers"]
        if item["value"] is not True or not isinstance(pointers, list) or pointers != list(assertions[name]):
            raise DocumentError("semantic_attestation_invalid", "attestation assertion or exact pointer differs", {"assertion": name}, EXIT_CONFLICT)
        for pointer in pointers:
            _resolve_pointer(value, pointer)
    return attestation


def _semantic_input(operation: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"operation": operation, "input_schema": value["schema"], "input_digest": canonical_digest(value), "value": value}


def _load_json_argument(value: str, *, allow_stdin: bool = False) -> Any:
    if value == "@-":
        if not allow_stdin:
            raise DocumentError("usage_invalid", "stdin is not allowed")
        text = sys.stdin.read()
    elif value.startswith("@"):
        try:
            text = pathlib.Path(value[1:]).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise DocumentError("input_unavailable", "JSON input could not be read", {"path": value[1:]}, EXIT_NOT_FOUND) from error
    else:
        raise DocumentError("usage_invalid", "JSON input must use @file or @-")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise DocumentError("schema_invalid", "input is not valid JSON") from error


def validate_core_doctor(doctor: Any) -> dict[str, Any]:
    if isinstance(doctor, dict) and "ok" in doctor:
        if set(doctor) != {"ok", "result"} or doctor.get("ok") is not True or not isinstance(doctor.get("result"), dict):
            raise DocumentError("core_preflight_invalid", "core doctor public envelope is invalid", exit_code=EXIT_CONFLICT)
        doctor = doctor["result"]
    required = {
        "schema", "owner", "supported_protocols", "repository_state", "root", "issues", "warnings",
        "plugin_version", "entrypoint", "protocol",
    }
    if not isinstance(doctor, dict) or set(doctor) != required:
        raise DocumentError("core_preflight_invalid", "core doctor fields differ from context-core-doctor/v1", exit_code=EXIT_CONFLICT)
    protocols = doctor.get("supported_protocols")
    issues = doctor.get("issues")
    warnings = doctor.get("warnings")
    entrypoint = doctor.get("entrypoint")
    try:
        resolved_entrypoint = required_core_surface(entrypoint) if isinstance(entrypoint, str) else None
        surface_version = core_surface_version(resolved_entrypoint) if resolved_entrypoint is not None else None
    except (DocumentError, OSError, RuntimeError):
        resolved_entrypoint = None
        surface_version = None
    required_suffix = pathlib.PurePosixPath(REQUIRED_PLUGIN["entrypoint"]).parts
    if (
        doctor.get("schema") != "context-core-doctor/v1"
        or doctor.get("owner") != "context-core"
        or doctor.get("root") != "context/"
        or doctor.get("plugin_version") != surface_version
        or doctor.get("protocol") != PROTOCOL
        or resolved_entrypoint is None
        or str(resolved_entrypoint) != entrypoint
        or tuple(resolved_entrypoint.parts[-len(required_suffix):]) != required_suffix
        or doctor.get("repository_state") not in {"absent", "partial", "invalid", "ready"}
        or not isinstance(protocols, list)
        or not protocols
        or len(protocols) != len(set(protocols))
        or any(not isinstance(item, str) or not item for item in protocols)
        or not isinstance(issues, list)
        or not isinstance(warnings, list)
    ):
        raise DocumentError("core_preflight_invalid", "core doctor identity or field shape is invalid", exit_code=EXIT_CONFLICT)
    for label, diagnostics in (("issues", issues), ("warnings", warnings)):
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("code"), str)
            or not item["code"]
            or any(not isinstance(key, str) for key in item)
            for item in diagnostics
        ):
            raise DocumentError("core_preflight_invalid", f"core doctor {label} diagnostics are invalid", exit_code=EXIT_CONFLICT)
    if doctor["repository_state"] == "ready" and issues:
        raise DocumentError("core_preflight_invalid", "ready core doctor must have no issues", exit_code=EXIT_CONFLICT)
    return doctor


def classify_core_preflight(inventory: Any, doctor: Any) -> dict[str, Any]:
    if not isinstance(inventory, dict) or not isinstance(inventory.get("plugins"), list):
        raise DocumentError("core_preflight_invalid", "core inventory or doctor receipt is invalid", exit_code=EXIT_CONFLICT)
    doctor = validate_core_doctor(doctor)
    plugins = [item for item in inventory["plugins"] if isinstance(item, dict) and item.get("plugin") == REQUIRED_PLUGIN["plugin"]]
    exact = [item for item in plugins if item.get("marketplace") == REQUIRED_PLUGIN["marketplace"]]
    if len(exact) > 1:
        raise DocumentError("core_preflight_invalid", "exact context-core inventory coordinate is ambiguous", exit_code=EXIT_CONFLICT)
    plugin = exact[0] if len(exact) == 1 else (plugins[0] if len(plugins) == 1 else None)
    observed = {
        "marketplace": plugin.get("marketplace") if plugin else None,
        "plugin": plugin.get("plugin") if plugin else None,
        "source": plugin.get("source") if plugin else None,
        "enabled": plugin.get("enabled") if plugin else None,
        "protocol": (plugin.get("protocols") or [None])[0] if plugin else None,
        "repository_state": doctor.get("repository_state"),
        "entrypoint": plugin.get("entrypoint") if plugin else None,
    }
    if plugin is None:
        code = "core_missing"
    elif plugin.get("marketplace") != REQUIRED_PLUGIN["marketplace"] or plugin.get("source") != REQUIRED_PLUGIN["source"]:
        code = "core_source_mismatch"
    elif plugin.get("enabled") is not True:
        code = "core_disabled"
    elif PROTOCOL not in plugin.get("protocols", []) or PROTOCOL not in doctor.get("supported_protocols", []):
        code = "core_incompatible"
    elif doctor.get("repository_state") == "absent":
        code = "core_uninitialized"
    elif doctor.get("repository_state") == "ready":
        code = "ready"
    elif doctor.get("repository_state") == "partial":
        code = "core_partial"
    elif doctor.get("repository_state") == "invalid":
        code = "core_invalid"
    else:
        raise DocumentError("core_preflight_invalid", "repository_state is invalid", exit_code=EXIT_CONFLICT)
    return {"code": code, "host": None, "observed": observed}


def require_core_preflight(args: argparse.Namespace, *, allow_absent: bool = False, allow_partial: bool = False) -> dict[str, Any]:
    if not args.host or not args.core_inventory or not args.core_doctor:
        raise DocumentError("core_preflight_required", "non-static DOCUMENT operations require host inventory and core doctor receipt", {"required_plugin": REQUIRED_PLUGIN, "write_policy": {"repository": "none", "host_configuration": "none"}}, EXIT_CONFLICT)
    result = classify_core_preflight(_load_json_argument(args.core_inventory), _load_json_argument(args.core_doctor))
    result["host"] = args.host
    allowed = result["code"] == "ready" or (allow_absent and result["code"] == "core_uninitialized") or (allow_partial and result["code"] == "core_partial")
    if not allowed:
        raise DocumentError(result["code"], "exact context-core preflight failed", {"observed": result["observed"], "required_plugin": REQUIRED_PLUGIN, "write_policy": {"repository": "none", "host_configuration": "none"}}, EXIT_CONFLICT)
    return result


def _bobbin_settings(method, *args):
    path = pathlib.Path(__file__).resolve().parents[2] / "context/scripts/bobbin_config.py"
    spec = importlib.util.spec_from_file_location("bobbin_document_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return getattr(module, method)(*args)
    except module.ConfigError as error:
        raise DocumentError(error.code, str(error), exit_code=EXIT_CONFLICT) from error


def vault_root(vault: str | None = None) -> pathlib.Path:
    try:
        if vault is not None:
            root = pathlib.Path(vault).expanduser().resolve(strict=True)
            if not root.is_dir():
                raise OSError("vault root is not a directory")
            return root
        configured = _bobbin_settings("configured_vault")
        if configured is not None:
            return configured
        cwd = pathlib.Path.cwd().resolve(strict=True)
        for root in (cwd, *cwd.parents):
            try:
                (root / "context").lstat()
            except FileNotFoundError:
                continue
            return root
        return cwd
    except (OSError, RuntimeError) as error:
        raise DocumentError("vault_not_found", "vault must be an existing directory", exit_code=EXIT_NOT_FOUND) from error


def _extract_block(text: str, block: str) -> list[str]:
    begin = f"<!-- BEGIN CONTEXT GENERATED:{block} -->"
    end = f"<!-- END CONTEXT GENERATED:{block} -->"
    if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) > text.index(end):
        raise DocumentError("index_stale", "document index block is malformed", {"block": block}, EXIT_INTEGRITY)
    return [line for line in text.split(begin, 1)[1].split(end, 1)[0].strip("\n").split("\n") if line]


if __name__ == "__main__":
    raise SystemExit(main())
