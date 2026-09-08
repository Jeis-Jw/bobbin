#!/usr/bin/env python3
"""context-term v1 semantic owner (Python 3.11+, stdlib only, write-free)."""
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
TERM_INDEX = "context/term/term.index.md"
SIGNAL = "term-encountered"
MAX_OWNER_INPUT_BYTES = 8 * 1024
MAX_CANDIDATE_BYTES = 16 * 1024
MAX_PUBLIC_OUTPUT_BYTES = 32 * 1024
MAX_PRIMARY_CLAIM_CODEPOINTS = 2000
ID_RE = re.compile(r"^ctx_[0-9a-f]{32}$")
LOCAL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
ENTRY_RE = re.compile(r"^.*<!-- context-entry (\{.*\}) -->$")
SECTIONS = ("Definition",)
REQUIRED_SECTIONS = ("Definition",)
LEGACY_SECTION_ALIASES = {"정의": "Definition"}
LEGACY_SECTIONS = tuple(LEGACY_SECTION_ALIASES)
PLACEHOLDERS = {"...", "TODO", "TBD", "N/A", "해당 없음"}
CANDIDATE_FIELDS = {
    "schema", "candidate_id", "title", "claim", "summary", "captured_from", "requested_kind",
    "specialized_kinds", "fallback_kind", "owner_inputs", "scope_hint", "evidence", "tags",
    "search_terms", "source_refs",
}
KNOWN_OWNER_KINDS = {"term", "assumption", "observation", "decision", "snapshot"}
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


class TermError(Exception):
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
    spec = importlib.util.spec_from_file_location("context_term_core_compatibility", helper)
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


def _section_value(sections: dict[str, str], canonical: str = "Definition") -> str:
    if canonical in sections:
        return sections[canonical]
    return sections.get("정의", "")


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalized_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _canonical_slug_part(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TermError("slot_invalid", f"{field} must be a string")
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
        raise TermError("slot_invalid", f"{field} is empty, reserved, or too long", {"field": field})
    return result


def canonical_scope(value: Any) -> str:
    if not isinstance(value, str):
        raise TermError("scope_invalid", "scope must be a string")
    stripped = normalized_key(value.strip()).strip("/")
    if not stripped or "//" in stripped:
        raise TermError("scope_invalid", "scope is empty or contains an empty segment")
    raw_parts = stripped.split("/")
    if len(raw_parts) > 8:
        raise TermError("scope_invalid", "scope has more than eight segments")
    parts = [_canonical_slug_part(part, field="scope segment", maximum=40) for part in raw_parts]
    result = "/".join(parts)
    if len(result) > 160:
        raise TermError("scope_invalid", "scope exceeds 160 codepoints")
    return result


def canonical_term_key(value: Any) -> str:
    if not isinstance(value, str) or "/" in value:
        raise TermError("term_key_invalid", "term must be a single project terminology slot")
    return _canonical_slug_part(value, field="term_key", maximum=80)


def is_ancestor_scope(ancestor: str, descendant: str) -> bool:
    left = canonical_scope(ancestor).split("/")
    right = canonical_scope(descendant).split("/")
    return len(left) < len(right) and right[: len(left)] == left


def scopes_overlap(left: str, right: str) -> bool:
    left = canonical_scope(left)
    right = canonical_scope(right)
    return left == right or is_ancestor_scope(left, right) or is_ancestor_scope(right, left)


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
                raise TermError("canonical_json_invalid", "object keys must be strings")
            key = nfc(raw_key)
            if key in output:
                raise TermError("canonical_json_invalid", "NFC-normalized keys collide", {"key": key})
            output[key] = _canonical_value(raw_value)
        return {key: output[key] for key in sorted(output)}
    raise TermError("canonical_json_invalid", "unsupported canonical JSON scalar", {"type": type(value).__name__})


def canonical_json(value: Any) -> str:
    return json.dumps(_canonical_value(value), ensure_ascii=False, separators=(",", ":"))


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _serialize_public(value: Any) -> str:
    text = canonical_json(value) + "\n"
    size = len(text.encode("utf-8"))
    if size > MAX_PUBLIC_OUTPUT_BYTES:
        raise TermError(
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
        raise TermError("core_surface_mismatch", "context-core version is missing", exit_code=EXIT_CONFLICT)
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value)
    if match is None:
        raise TermError("core_surface_mismatch", "context-core version is invalid", exit_code=EXIT_CONFLICT)
    return tuple(int(part) for part in match.groups())


def core_surface_version(resolved: pathlib.Path) -> str:
    suffix = pathlib.PurePosixPath(REQUIRED_PLUGIN["entrypoint"]).parts
    plugin_root = resolved.parents[len(suffix) - 1]
    versions: set[str] = set()
    for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        try:
            manifest = json.loads((plugin_root / relative).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise TermError("core_surface_mismatch", "context-core host manifests are unavailable or invalid", {"manifest": relative}, EXIT_CONFLICT) from error
        version = manifest.get("version") if isinstance(manifest, dict) else None
        if not isinstance(manifest, dict) or manifest.get("name") != "bobbin":
            raise TermError("core_surface_mismatch", "context-core manifest identity is invalid", exit_code=EXIT_CONFLICT)
        major, _, _ = _version_parts(version)
        if major != CORE_COMPATIBLE_MAJOR:
            raise TermError("core_surface_mismatch", "context-core major version is incompatible", {"required_major": CORE_COMPATIBLE_MAJOR, "observed_version": version}, EXIT_CONFLICT)
        versions.add(version)
    if len(versions) != 1:
        raise TermError("core_surface_mismatch", "context-core host manifests use different versions", exit_code=EXIT_CONFLICT)
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
        raise TermError(
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
        raise TermError(
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
    except TermError as error:
        if error.code == "core_surface_mismatch":
            error.message += "; choose a listed candidate and start a new session"
            error.details = _core_failure_details(value, **error.details)
        raise
    if expected_sha256 is not None and digest != expected_sha256:
        raise TermError("core_surface_changed", "context-core entrypoint changed during the operation", {"observed_sha256": digest}, EXIT_CONFLICT)
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
        raise TermError(
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
        raise TermError("schema_invalid", f"{field} must be a string")
    result = nfc(value.strip())
    if not result or result in PLACEHOLDERS:
        raise TermError("schema_invalid", f"{field} is empty or a placeholder")
    if len(result) > maximum:
        raise TermError(
            "schema_invalid",
            f"{field} exceeds its {maximum}-codepoint limit",
            {"field": field, **_codepoint_size_details(len(result), maximum)},
            EXIT_CONFLICT,
        )
    return result


def _string_list(value: Any, field: str, *, minimum: int = 0, maximum: int = 12, item_maximum: int = 500) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise TermError("schema_invalid", f"{field} list bounds are invalid")
    result = [_substantive(item, maximum=item_maximum, field=field) for item in value]
    if len(result) != len(set(result)):
        raise TermError("schema_invalid", f"{field} contains duplicates")
    return result


def _valid_context_id(value: Any) -> bool:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        return False
    parsed = uuid.UUID(hex=value[4:])
    return parsed.version == 4 and parsed.variant == uuid.RFC_4122


def require_context_id(value: Any, field: str = "id") -> str:
    if not _valid_context_id(value):
        raise TermError("id_invalid", f"{field} must be ctx_ plus lowercase UUIDv4 hex", {"field": field})
    return value


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TermError("schema_invalid", f"{field} must be a timestamp")
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as error:
        raise TermError("schema_invalid", f"{field} must be RFC3339-compatible") from error
    if parsed.tzinfo is None or parsed.isoformat(timespec="seconds") != value:
        raise TermError("schema_invalid", f"{field} must include an offset and seconds precision")
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
        raise TermError("filename_invalid", "title cannot produce a safe term filename")
    return filename


def owner_descriptor() -> dict[str, Any]:
    return {
        "schema": "context-owner-descriptor/v2",
        "owner": "context-term",
        "kind": "term",
        "artifact_schema": "context-term/v1",
        "authority": "authoritative",
        "structural_profile": {
            "schema": "context-structural-profile/v1",
            "fields": {
                "scope": {"type": "string", "required": True, "min_chars": 1, "max_chars": 160},
                "term": {"type": "string", "required": True, "min_chars": 1, "max_chars": 120},
                "term_key": {"type": "string", "required": True, "min_chars": 1, "max_chars": 80},
                "aliases": {"type": "string_list", "required": False, "min_items": 0, "max_items": 12, "max_item_chars": 120},
                "deprecated_terms": {"type": "string_list", "required": False, "min_items": 0, "max_items": 12, "max_item_chars": 120},
                "related": {"type": "string_list", "required": False, "min_items": 0, "max_items": 12, "max_item_chars": 120},
                "retired_at": {"type": "timestamp", "required": False},
                "retired_reason": {"type": "enum", "required": False, "values": ["deprecated", "superseded"]},
                "deprecation_reason": {"type": "string", "required": False, "min_chars": 1, "max_chars": 800},
                "replacement_term": {"type": "string", "required": False, "min_chars": 1, "max_chars": 120},
                "superseded_by": {"type": "context_id", "required": False},
                "supersedes": {"type": "context_id_list", "required": False, "min_items": 0, "max_items": 12},
            },
            # Keep the registered v2 descriptor byte-compatible with existing repositories.
            # Core treats this name as a legacy alias while new artifacts use English headings.
            "sections": {"ordered": list(LEGACY_SECTIONS), "required": list(LEGACY_SECTIONS), "primary": "정의"},
            "index_projection": ["scope", "term_key", "term"],
            "lifecycle": {
                "allowed_topologies": ["create_current", "replace_same_state", "retire_current", "supersede_current"],
                "reasons": {
                    "deprecated": {
                        "topology": "retire_current",
                        "required_fields": ["retired_at", "retired_reason", "deprecation_reason"],
                        "forbidden_fields": ["superseded_by"],
                        "successor": "forbidden",
                        "references": [],
                    },
                    "superseded": {
                        "topology": "supersede_current",
                        "required_fields": ["retired_at", "retired_reason", "superseded_by"],
                        "forbidden_fields": ["deprecation_reason", "replacement_term"],
                        "successor": "required",
                        "references": [
                            {"location": "predecessor", "field": "superseded_by", "target": "successor", "match": "equals"},
                            {"location": "successor", "field": "supersedes", "target": "predecessor", "match": "contains"},
                        ],
                    },
                },
            },
        },
    }


def term_capability() -> dict[str, Any]:
    descriptor = owner_descriptor()
    return {
        "schema": "context-owner-capability/v1",
        "owner": "context-term",
        "kind": "term",
        "artifact_schema": "context-term/v1",
        "authority": "authoritative",
        "descriptor_digest": canonical_digest(descriptor),
        "claim_surface": {"type": "agent_skill", "name": "bobbin:term", "operation": "claim"},
        "claim_rule": "Claim only a term with an explicit project-specific or project-special meaning and definition",
        "claim_assertions": ["term_identified", "definition_present"],
        "lifecycle_operations": {
            "same_claim": {
                "surface": {"type": "agent_skill", "name": "bobbin:term", "operation": "same_claim"},
                "rule": "Both actual term and Definition bodies express the same project terminology claim",
                "assertions": ["same_semantic_claim"],
            }
        },
        "draft_fields": {
            "required": {
                "term": {"type": "string", "min_chars": 1, "max_chars": 120},
                "definition": {"type": "string", "min_chars": 1, "max_chars": 2000},
            },
            "optional": {
                "aliases": {"type": "string_list", "max_items": 12, "max_item_chars": 120},
                "deprecated_terms": {"type": "string_list", "max_items": 12, "max_item_chars": 120},
                "related": {"type": "string_list", "max_items": 12, "max_item_chars": 120},
            },
        },
    }


def schema_result() -> dict[str, Any]:
    return {
        "schema": "context-term-schema/v1",
        "protocol": PROTOCOL,
        "artifact_schema": "context-term/v1",
        "authority": "authoritative",
        "owner_descriptor": owner_descriptor(),
        "features": [REQUIRED_FEATURE, "exact-rfc6901-claim-binding", "project-term-gate", "scope-term-key-slot", "signal-gated-read"],
        "workflow_surface": {
            "entrypoint": "term_workflow.py",
            "commands": ["preview", "apply"],
            "preview_input_modes": ["inline"],
            "preflight": "derived_from_verified_core_manifests_and_doctor",
            "preview_state": "awaiting_approval",
        },
        "physical_write": False,
    }


def _validate_term_vocabulary(term: str, aliases: Sequence[str], deprecated_terms: Sequence[str]) -> None:
    primary = canonical_term_key(term)
    alias_keys = [canonical_term_key(item) for item in aliases]
    deprecated_keys = [canonical_term_key(item) for item in deprecated_terms]
    if len(alias_keys) != len(set(alias_keys)) or len(deprecated_keys) != len(set(deprecated_keys)):
        raise TermError("term_overlap", "aliases or deprecated_terms collide after canonicalization", exit_code=EXIT_CONFLICT)
    if primary in alias_keys or primary in deprecated_keys or set(alias_keys) & set(deprecated_keys):
        raise TermError("term_overlap", "term, aliases, and deprecated_terms must occupy disjoint canonical keys", exit_code=EXIT_CONFLICT)


def render_document(frontmatter: dict[str, Any], sections: dict[str, str]) -> str:
    required = {"schema", "id", "title", "summary", "created_at", "captured_from", "scope", "term", "term_key"}
    optional = {
        "updated_at", "tags", "search_terms", "source_refs", "aliases", "deprecated_terms", "related",
        "retired_at", "retired_reason", "deprecation_reason", "replacement_term", "superseded_by", "supersedes",
    }
    if required - set(frontmatter) or set(frontmatter) - required - optional:
        raise TermError("schema_invalid", "term frontmatter fields are incomplete or unknown")
    if frontmatter.get("schema") != "context-term/v1":
        raise TermError("schema_invalid", "artifact schema must be context-term/v1")
    require_context_id(frontmatter.get("id"))
    for name, maximum in (("title", 120), ("summary", 280)):
        value = _substantive(frontmatter.get(name), maximum=maximum, field=name)
        if "\n" in value:
            raise TermError("schema_invalid", f"{name} must be one line")
    scope = canonical_scope(frontmatter.get("scope"))
    term = _substantive(frontmatter.get("term"), maximum=120, field="term")
    if "\n" in term or scope != frontmatter["scope"] or canonical_term_key(term) != frontmatter.get("term_key"):
        raise TermError("slot_invalid", "stored scope and term_key must be canonical and derived from term", exit_code=EXIT_CONFLICT)
    created_at = _timestamp(frontmatter.get("created_at"), "created_at")
    if frontmatter.get("captured_from") not in {"conversation", "workspace", "manual", "import"}:
        raise TermError("schema_invalid", "captured_from is invalid")
    created = datetime.datetime.fromisoformat(created_at)
    for field in ("updated_at", "retired_at"):
        if field in frontmatter:
            observed = datetime.datetime.fromisoformat(_timestamp(frontmatter[field], field))
            if observed < created:
                raise TermError("clock_invalid", f"{field} cannot precede created_at", exit_code=EXIT_CONFLICT)
    for field in ("tags", "search_terms"):
        if field in frontmatter:
            _string_list(frontmatter[field], field, maximum=12, item_maximum=40)
    if "source_refs" in frontmatter:
        _string_list(frontmatter["source_refs"], "source_refs", maximum=12, item_maximum=500)
    vocabulary: dict[str, list[str]] = {}
    for field in ("aliases", "deprecated_terms", "related"):
        vocabulary[field] = _string_list(frontmatter.get(field, []), field, maximum=12, item_maximum=120)
    _validate_term_vocabulary(term, vocabulary["aliases"], vocabulary["deprecated_terms"])
    if "supersedes" in frontmatter:
        for identifier in _string_list(frontmatter["supersedes"], "supersedes", maximum=12, item_maximum=36):
            require_context_id(identifier, "supersedes")
    if "superseded_by" in frontmatter:
        require_context_id(frontmatter["superseded_by"], "superseded_by")
    history = "retired_at" in frontmatter or "retired_reason" in frontmatter
    if history:
        if not {"retired_at", "retired_reason"}.issubset(frontmatter):
            raise TermError("lifecycle_invalid", "retired lifecycle fields must appear together")
        reason = frontmatter["retired_reason"]
        if reason == "deprecated":
            _substantive(frontmatter.get("deprecation_reason"), maximum=800, field="deprecation_reason")
            if "superseded_by" in frontmatter:
                raise TermError("lifecycle_invalid", "deprecated term cannot have a successor artifact edge")
            if "replacement_term" in frontmatter:
                replacement = _substantive(frontmatter["replacement_term"], maximum=120, field="replacement_term")
                if canonical_term_key(replacement) == frontmatter["term_key"]:
                    raise TermError("lifecycle_invalid", "replacement_term must name a different canonical term")
        elif reason == "superseded":
            if "superseded_by" not in frontmatter or any(field in frontmatter for field in ("deprecation_reason", "replacement_term")):
                raise TermError("lifecycle_invalid", "superseded term requires only its successor edge")
        else:
            raise TermError("lifecycle_invalid", "retired_reason is invalid")
    elif any(field in frontmatter for field in ("deprecation_reason", "replacement_term", "superseded_by")):
        raise TermError("lifecycle_invalid", "Current term contains retired-only fields")
    if len(sections) != 1 or {_canonical_section_name(name) for name in sections} != set(REQUIRED_SECTIONS):
        raise TermError("schema_invalid", "term must contain exactly the definition section")
    heading = next(iter(sections))
    definition = _substantive(_section_value(sections), maximum=4000, field="definition")
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}")
    lines.extend(["---", "", f"## {heading}", "", definition, ""])
    return "\n".join(lines).rstrip("\n") + "\n"


def parse_document(text: str) -> tuple[dict[str, Any], dict[str, str]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0] != "---":
        raise TermError("schema_invalid", "artifact frontmatter is missing", exit_code=EXIT_INTEGRITY)
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise TermError("schema_invalid", "artifact frontmatter is unterminated", exit_code=EXIT_INTEGRITY) from error
    frontmatter: dict[str, Any] = {}
    for line in lines[1:closing]:
        if ": " not in line:
            raise TermError("schema_invalid", "artifact frontmatter is malformed", exit_code=EXIT_INTEGRITY)
        key, raw = line.split(": ", 1)
        if key in frontmatter:
            raise TermError("schema_invalid", "artifact frontmatter key is duplicated", {"field": key}, EXIT_INTEGRITY)
        try:
            frontmatter[key] = json.loads(raw)
        except json.JSONDecodeError as error:
            raise TermError("schema_invalid", "artifact frontmatter JSON is malformed", {"field": key}, EXIT_INTEGRITY) from error
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in lines[closing + 1:]:
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = line[3:]
            if current in sections:
                raise TermError("schema_invalid", "artifact section is duplicated", {"section": current}, EXIT_INTEGRITY)
            buffer = []
        elif current is not None:
            buffer.append(line)
        elif line.strip():
            raise TermError("schema_invalid", "content exists outside an H2 section", exit_code=EXIT_INTEGRITY)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    canonical = render_document(frontmatter, sections)
    if file_bytes(canonical) != file_bytes(text):
        raise TermError("schema_invalid", "artifact is not canonical", exit_code=EXIT_INTEGRITY)
    return frontmatter, sections


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
        raise TermError("candidate_invalid", "candidate envelope is incomplete", exit_code=EXIT_CONFLICT)
    candidate_bytes = len(canonical_json(candidate).encode("utf-8"))
    if candidate_bytes > MAX_CANDIDATE_BYTES:
        raise TermError(
            "candidate_too_large",
            "candidate exceeds the 16 KiB protocol budget",
            _byte_size_details(candidate_bytes, MAX_CANDIDATE_BYTES),
            EXIT_CONFLICT,
        )
    if not isinstance(candidate.get("candidate_id"), str) or re.fullmatch(r"cand_[0-9a-f]{32}", candidate["candidate_id"]) is None:
        raise TermError("candidate_invalid", "candidate_id is invalid")
    for name, maximum in (("title", 120), ("claim", MAX_PRIMARY_CLAIM_CODEPOINTS), ("summary", 280)):
        _substantive(candidate.get(name), maximum=maximum, field=name)
    if candidate.get("captured_from") not in {"conversation", "workspace", "manual", "import"}:
        raise TermError("candidate_invalid", "captured_from is invalid")
    specialized = candidate.get("specialized_kinds")
    if not isinstance(specialized, list) or len(specialized) > 2 or len(specialized) != len(set(specialized)) or not all(isinstance(item, str) for item in specialized):
        raise TermError("candidate_invalid", "specialized_kinds is invalid")
    if candidate.get("fallback_kind") not in {None, "observation", "snapshot"}:
        raise TermError("candidate_invalid", "fallback_kind is invalid")
    requested = candidate.get("requested_kind")
    if requested is not None and not isinstance(requested, str):
        raise TermError("candidate_invalid", "requested_kind must be a string or null")
    owner_inputs = candidate.get("owner_inputs")
    if not isinstance(owner_inputs, dict) or set(owner_inputs) - KNOWN_OWNER_KINDS:
        raise TermError("candidate_invalid", "owner_inputs must be an object")
    relevant = set(specialized) | ({requested} if requested else set()) | ({candidate["fallback_kind"]} if candidate["fallback_kind"] else set())
    foreign_owner_inputs = set(owner_inputs) - {"term"}
    if not foreign_owner_inputs and set(owner_inputs) - relevant:
        raise TermError("candidate_invalid", "owner_inputs contains a kind not offered by the candidate", exit_code=EXIT_CONFLICT)
    for kind, value in owner_inputs.items():
        if not isinstance(value, dict):
            raise TermError("candidate_invalid", "each owner input must be an object", {"kind": kind}, EXIT_CONFLICT)
        owner_input_bytes = len(canonical_json(value).encode("utf-8"))
        if owner_input_bytes > MAX_OWNER_INPUT_BYTES:
            raise TermError(
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
    """Return the non-TERM semantic kind that makes this candidate a decline."""

    offered = set(candidate.get("specialized_kinds", []))
    if candidate.get("requested_kind"):
        offered.add(candidate["requested_kind"])
    offered.update(candidate.get("owner_inputs", {}))
    foreign = sorted(offered - {"term"})
    return foreign[0] if foreign else None


def validate_candidate_batch(batch: Any) -> dict[str, Any]:
    if (
        not isinstance(batch, dict)
        or set(batch) != {"schema", "audit_count", "candidates"}
        or batch.get("schema") != "context-capture-batch/v1"
        or batch.get("audit_count") != 1
        or not isinstance(batch.get("candidates"), list)
    ):
        raise TermError("candidate_invalid", "candidate batch envelope is invalid", exit_code=EXIT_CONFLICT)
    candidates = batch["candidates"]
    if len(candidates) > 8:
        raise TermError("candidate_batch_too_large", "candidate batch exceeds eight items", exit_code=EXIT_CONFLICT)
    batch_bytes = len(canonical_json(batch).encode("utf-8"))
    if batch_bytes > MAX_CANDIDATE_BYTES:
        raise TermError(
            "candidate_batch_too_large",
            "candidate batch exceeds the 16 KiB protocol budget",
            _byte_size_details(batch_bytes, MAX_CANDIDATE_BYTES),
            EXIT_CONFLICT,
        )
    for candidate in candidates:
        validate_transport_candidate(candidate)
    identifiers = [candidate["candidate_id"] for candidate in candidates]
    if len(identifiers) != len(set(identifiers)):
        raise TermError("candidate_invalid", "candidate batch contains duplicate candidate_id", exit_code=EXIT_CONFLICT)
    return {
        "schema": "context-term-candidate-batch-validation/v1",
        "status": "valid",
        "count": len(candidates),
        "canonical_bytes": batch_bytes,
        "physical_write": False,
    }


def validate_term_candidate(candidate: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = validate_transport_candidate(candidate)
    boundary = _foreign_semantic_boundary(candidate)
    if boundary is not None:
        raise TermError("owner_decline", f"candidate belongs to the {boundary} semantic boundary", {"kind": boundary}, EXIT_CONFLICT)
    if candidate.get("requested_kind") != "term" and "term" not in candidate.get("specialized_kinds", []):
        raise TermError("owner_decline", "candidate is not offered to the term owner", exit_code=EXIT_CONFLICT)
    owner_input = candidate["owner_inputs"].get("term")
    allowed = {"term", "definition", "project_signal", "aliases", "deprecated_terms", "related"}
    required = {"term", "definition", "project_signal"}
    if not isinstance(owner_input, dict) or set(owner_input) - allowed or required - set(owner_input):
        raise TermError("candidate_invalid", "term owner input fields are incomplete or unknown", exit_code=EXIT_CONFLICT)
    if owner_input["project_signal"] not in {"project-specific", "project-special-meaning"}:
        raise TermError("owner_decline", "generic dictionary meaning is outside TERM authority", exit_code=EXIT_CONFLICT)
    term = _substantive(owner_input["term"], maximum=120, field="term")
    definition = _substantive(owner_input["definition"], maximum=2000, field="definition")
    if candidate["claim"] != definition:
        raise TermError("candidate_invalid", "candidate claim must equal the actual project definition", exit_code=EXIT_CONFLICT)
    aliases = _string_list(owner_input.get("aliases", []), "aliases", maximum=12, item_maximum=120)
    deprecated_terms = _string_list(owner_input.get("deprecated_terms", []), "deprecated_terms", maximum=12, item_maximum=120)
    related = _string_list(owner_input.get("related", []), "related", maximum=12, item_maximum=120)
    _validate_term_vocabulary(term, aliases, deprecated_terms)
    normalized = {
        "term": term,
        "definition": definition,
        "project_signal": owner_input["project_signal"],
        "aliases": aliases,
        "deprecated_terms": deprecated_terms,
        "related": related,
    }
    scope = canonical_scope(candidate.get("scope_hint"))
    return normalized, {"scope": scope, "term_key": canonical_term_key(term)}


def _resolve_pointer(value: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise TermError("semantic_attestation_invalid", "evidence pointer must be RFC 6901", exit_code=EXIT_CONFLICT)
    current = value
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if "~" in token:
            raise TermError("semantic_attestation_invalid", "evidence pointer escape is invalid", exit_code=EXIT_CONFLICT)
        try:
            current = current[int(token)] if isinstance(current, list) else current[token]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise TermError("semantic_attestation_invalid", "evidence pointer does not resolve", {"pointer": pointer}, EXIT_CONFLICT) from error
    if current in (None, "", [], {}, False):
        raise TermError("semantic_attestation_invalid", "evidence pointer resolves to an empty value", {"pointer": pointer}, EXIT_CONFLICT)
    return current


def validate_attestation(attestation: Any, value: dict[str, Any], operation: str, assertions: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    envelope_fields = {"schema", "operation", "input_schema", "input_digest", "assertions"}
    if (
        not isinstance(attestation, dict)
        or set(attestation) != envelope_fields
        or attestation.get("schema") != "context-semantic-attestation/v1"
    ):
        raise TermError("semantic_attestation_invalid", "attestation envelope is invalid", exit_code=EXIT_CONFLICT)
    if (
        not isinstance(value, dict)
        or attestation.get("operation") != operation
        or attestation.get("input_schema") != value.get("schema")
        or attestation.get("input_digest") != canonical_digest(value)
    ):
        raise TermError("semantic_attestation_invalid", "attestation is not bound to the exact semantic input", exit_code=EXIT_CONFLICT)
    items = attestation.get("assertions")
    assertion_fields = {"name", "value", "evidence_pointers"}
    if (
        not isinstance(items, list)
        or len(items) != len(assertions)
        or any(not isinstance(item, dict) or set(item) != assertion_fields for item in items)
    ):
        raise TermError("semantic_attestation_invalid", "attestation assertions are not exact objects", exit_code=EXIT_CONFLICT)
    names = [item["name"] for item in items]
    if any(not isinstance(name, str) for name in names) or len(names) != len(set(names)) or set(names) != set(assertions):
        raise TermError("semantic_attestation_invalid", "attestation assertions differ from the owner contract", exit_code=EXIT_CONFLICT)
    for item in items:
        name = item["name"]
        pointers = item["evidence_pointers"]
        if item["value"] is not True or not isinstance(pointers, list) or pointers != list(assertions[name]):
            raise TermError("semantic_attestation_invalid", "attestation assertion or exact pointer differs", {"assertion": name}, EXIT_CONFLICT)
        for pointer in pointers:
            _resolve_pointer(value, pointer)
    return attestation


def _semantic_input(operation: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"operation": operation, "input_schema": value["schema"], "input_digest": canonical_digest(value), "value": value}


def _sections_from_input(owner_input: dict[str, Any]) -> dict[str, str]:
    return {"Definition": owner_input["definition"]}


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
        return build_decline_result(candidate, f"{boundary} semantic boundary is not project terminology")
    raw_owner_input = candidate.get("owner_inputs", {}).get("term")
    if not isinstance(raw_owner_input, dict) or raw_owner_input.get("project_signal") not in {"project-specific", "project-special-meaning"}:
        return build_decline_result(candidate, "generic dictionary meaning has no explicit project terminology signal")
    owner_input, structural = validate_term_candidate(candidate)
    validate_attestation(
        attestation,
        candidate,
        "claim",
        {
            "term_identified": ("/owner_inputs/term/term",),
            "definition_present": ("/owner_inputs/term/definition",),
        },
    )
    drafts: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    if not route_only:
        identifier = require_context_id(identifier or new_context_id())
        created_at = _timestamp(created_at or now_rfc3339(), "created_at")
        frontmatter: dict[str, Any] = {
            "schema": "context-term/v1",
            "id": identifier,
            "title": _substantive(candidate["title"], maximum=120, field="title"),
            "summary": _substantive(candidate["summary"], maximum=280, field="summary"),
            "created_at": created_at,
            "captured_from": candidate["captured_from"],
            "scope": structural["scope"],
            "term": owner_input["term"],
            "term_key": structural["term_key"],
        }
        for field in ("tags", "search_terms", "source_refs"):
            if candidate.get(field):
                frontmatter[field] = _string_list(candidate[field], field, maximum=12, item_maximum=500 if field == "source_refs" else 40)
        for field in ("aliases", "deprecated_terms", "related"):
            if owner_input[field]:
                frontmatter[field] = owner_input[field]
        sections = _sections_from_input(owner_input)
        content = render_document(frontmatter, sections)
        path = "context/term/" + (filename or natural_filename(candidate["title"]))
        effect_id = "effect_create_term"
        drafts = [{
            "effect_id": effect_id,
            "path": path,
            "content": content,
            "semantic_projection": {"kind": "term", "primary_claim": sections["Definition"], "supporting_context": [owner_input["term"]]},
        }]
        effects = [{"effect_id": effect_id, "action": "create", "area": "term", "id": identifier, "state": "current"}]
        operations = [{"op": "create", "effect_id": effect_id, "area": "term", "path": path}]
    result = {
        "schema": "context-owner-result/v1",
        "result_type": "claim",
        "transition": "capture",
        "owner": "context-term",
        "target_kind": "term",
        "candidate_id": candidate["candidate_id"],
        "decision": "claim",
        "reason": "explicit project-specific terminology",
        "capability_digest": canonical_digest(term_capability()),
        "semantic_inputs": [_semantic_input("claim", candidate)],
        "semantic_attestations": [attestation],
        "artifact_drafts": drafts,
        "effects": effects,
        "proposed_plan": {"schema": "context-owner-plan/v1", "transition": "capture", "operations": operations},
    }
    validate_owner_result(result)
    return result


def build_decline_result(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    candidate = validate_transport_candidate(candidate)
    return {
        "schema": "context-owner-result/v1",
        "result_type": "claim",
        "transition": "capture",
        "owner": "context-term",
        "target_kind": "term",
        "candidate_id": candidate["candidate_id"],
        "decision": "decline",
        "reason": _substantive(reason, maximum=500, field="reason"),
        "capability_digest": canonical_digest(term_capability()),
        "semantic_inputs": [_semantic_input("claim", candidate)],
        "semantic_attestations": [],
        "artifact_drafts": [],
        "effects": [],
        "proposed_plan": None,
    }


def _validate_projection(draft: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    frontmatter, sections = parse_document(draft.get("content", ""))
    projection = draft.get("semantic_projection")
    if not isinstance(projection, dict) or set(projection) != {"kind", "primary_claim", "supporting_context"}:
        raise TermError("owner_result_invalid", "semantic projection is invalid", exit_code=EXIT_CONFLICT)
    if (
        projection.get("kind") != "term"
        or projection.get("primary_claim") != _section_value(sections)
        or projection.get("supporting_context") != [frontmatter["term"]]
    ):
        raise TermError("owner_result_invalid", "semantic projection differs from actual term body", exit_code=EXIT_CONFLICT)
    return frontmatter, sections


def validate_owner_result(result: Any) -> None:
    required = {"schema", "result_type", "transition", "owner", "target_kind", "capability_digest", "semantic_inputs", "semantic_attestations", "artifact_drafts", "effects", "proposed_plan"}
    if not isinstance(result, dict) or result.get("schema") != "context-owner-result/v1" or required - set(result):
        raise TermError("owner_result_invalid", "owner result envelope is incomplete", exit_code=EXIT_CONFLICT)
    if result.get("owner") != "context-term" or result.get("target_kind") != "term" or result.get("capability_digest") != canonical_digest(term_capability()):
        raise TermError("capability_digest_mismatch", "owner result is not bound to the TERM capability", exit_code=EXIT_CONFLICT)
    raw_inputs = result.get("semantic_inputs")
    raw_attestations = result.get("semantic_attestations")
    if not isinstance(raw_inputs, list) or not isinstance(raw_attestations, list):
        raise TermError("owner_result_invalid", "semantic evidence collections must be lists", exit_code=EXIT_CONFLICT)
    inputs: dict[str, dict[str, Any]] = {}
    for item in raw_inputs:
        if not isinstance(item, dict) or not isinstance(item.get("value"), dict):
            raise TermError("semantic_input_invalid", "semantic input must be an exact object", exit_code=EXIT_CONFLICT)
        operation = item.get("operation")
        if operation in inputs or item.get("input_schema") != item.get("value", {}).get("schema") or item.get("input_digest") != canonical_digest(item.get("value")):
            raise TermError("semantic_input_invalid", "semantic input binding is invalid", exit_code=EXIT_CONFLICT)
        inputs[operation] = item
    if any(not isinstance(item, dict) for item in raw_attestations):
        raise TermError("semantic_attestation_invalid", "semantic attestation must be an object", exit_code=EXIT_CONFLICT)
    attestations = {item.get("operation"): item for item in raw_attestations}
    if len(attestations) != len(raw_attestations):
        raise TermError("semantic_attestation_invalid", "semantic attestation is duplicated", exit_code=EXIT_CONFLICT)
    if result["result_type"] == "claim" and result.get("decision") == "decline":
        if set(inputs) != {"claim"} or attestations or result.get("artifact_drafts") or result.get("effects") or result.get("proposed_plan") is not None:
            raise TermError("owner_result_invalid", "decline must bind only the exact candidate", exit_code=EXIT_CONFLICT)
        return
    if result["result_type"] == "claim":
        if result.get("decision") != "claim" or set(inputs) != {"claim"} or set(attestations) != {"claim"}:
            raise TermError("owner_result_invalid", "claim evidence is incomplete", exit_code=EXIT_CONFLICT)
        validate_attestation(attestations["claim"], inputs["claim"]["value"], "claim", {"term_identified": ("/owner_inputs/term/term",), "definition_present": ("/owner_inputs/term/definition",)})
    elif result["result_type"] == "mutation":
        expected_inputs = {
            "term_deprecate": {"mutation_request"},
            "term_annotate": {"mutation_request"},
            "term_supersede": {"claim", "same_claim", "mutation_request"},
        }.get(result.get("transition"))
        expected_attestations = {"claim", "same_claim"} if result.get("transition") == "term_supersede" else set()
        if expected_inputs is None or set(inputs) != expected_inputs or set(attestations) != expected_attestations:
            raise TermError("owner_result_invalid", "mutation semantic evidence is incomplete", exit_code=EXIT_CONFLICT)
        if result["transition"] == "term_supersede":
            validate_attestation(attestations["claim"], inputs["claim"]["value"], "claim", {"term_identified": ("/owner_inputs/term/term",), "definition_present": ("/owner_inputs/term/definition",)})
            validate_attestation(attestations["same_claim"], inputs["same_claim"]["value"], "same_claim", {"same_semantic_claim": ("/predecessor/primary_claim", "/successor/primary_claim")})
    else:
        raise TermError("owner_result_invalid", "owner result type is unsupported", exit_code=EXIT_CONFLICT)
    plan = result.get("proposed_plan")
    if not isinstance(plan, dict) or plan.get("schema") != "context-owner-plan/v1" or plan.get("transition") != result["transition"]:
        raise TermError("owner_result_invalid", "owner plan is invalid", exit_code=EXIT_CONFLICT)
    drafts, effects, operations = result.get("artifact_drafts"), result.get("effects"), plan.get("operations")
    if not all(isinstance(value, list) for value in (drafts, effects, operations)):
        raise TermError("owner_result_invalid", "plan collections are invalid", exit_code=EXIT_CONFLICT)
    for collection in (drafts, effects, operations):
        ids = [item.get("effect_id") for item in collection]
        if any(not isinstance(item, str) or not LOCAL_ID_RE.fullmatch(item) for item in ids) or len(ids) != len(set(ids)):
            raise TermError("plan_preview_mismatch", "effect ids are invalid", exit_code=EXIT_CONFLICT)
    if {item["effect_id"] for item in effects} != {item["effect_id"] for item in operations}:
        raise TermError("plan_preview_mismatch", "effects and operations are not 1:1", exit_code=EXIT_CONFLICT)
    draft_map = {draft["effect_id"]: _validate_projection(draft) for draft in drafts}
    for operation in operations:
        if operation.get("op") not in {"create", "replace", "move"} or operation.get("area") != "term":
            raise TermError("plan_preview_mismatch", "operation escapes the term area", exit_code=EXIT_CONFLICT)
        if operation["effect_id"] not in draft_map:
            raise TermError("plan_preview_mismatch", "operation lacks a complete draft", exit_code=EXIT_CONFLICT)
    if result["transition"] == "capture" and len(drafts) not in {0, 1}:
        raise TermError("plan_preview_mismatch", "capture creates at most one term", exit_code=EXIT_CONFLICT)
    if result["transition"] == "term_deprecate":
        if len(drafts) != 1 or "/retired/" not in drafts[0]["path"]:
            raise TermError("lifecycle_invalid", "deprecate must retire one term", exit_code=EXIT_CONFLICT)
        frontmatter, _ = draft_map[drafts[0]["effect_id"]]
        if frontmatter.get("retired_reason") != "deprecated" or "deprecation_reason" not in frontmatter:
            raise TermError("lifecycle_invalid", "deprecate reason differs from the artifact", exit_code=EXIT_CONFLICT)
    if result["transition"] == "term_supersede":
        current = [(draft, draft_map[draft["effect_id"]][0]) for draft in drafts if "/retired/" not in draft["path"]]
        history = [(draft, draft_map[draft["effect_id"]][0]) for draft in drafts if "/retired/" in draft["path"]]
        if len(current) != 1 or len(history) != 1:
            raise TermError("lifecycle_invalid", "supersede requires one predecessor and successor", exit_code=EXIT_CONFLICT)
        old, new = history[0][1], current[0][1]
        if (
            old.get("superseded_by") != new["id"]
            or old["id"] not in new.get("supersedes", [])
            or (old["scope"], old["term_key"]) != (new["scope"], new["term_key"])
        ):
            raise TermError("lifecycle_invalid", "supersede reciprocal edges are invalid", exit_code=EXIT_CONFLICT)


def term_index_seed() -> str:
    descriptor = canonical_json(owner_descriptor())
    return f'''---
schema: "context-area-index/v1"
index: true
area: "term"
owner: "context-term"
artifact_schema: "context-term/v1"
authority: "authoritative"
summary: "Manage project-specific terms and their canonical definitions."
search_terms: ["term","terminology","definition"]
projection_fields: ["scope","term_key","term"]
---

<!-- BEGIN CONTEXT GENERATED:owner-profile -->
{descriptor}
<!-- END CONTEXT GENERATED:owner-profile -->

# Term

## Current
<!-- BEGIN CONTEXT GENERATED:current -->
<!-- END CONTEXT GENERATED:current -->

## History
<!-- BEGIN CONTEXT GENERATED:history -->
<!-- END CONTEXT GENERATED:history -->
'''


def build_init_plan(preflight: dict[str, Any] | None = None) -> dict[str, Any]:
    descriptor = owner_descriptor()
    seed = term_index_seed()
    core_state = "ready" if preflight is None else preflight["observed"]["repository_state"]
    host = None if preflight is None else preflight.get("host")
    return {
        "schema": "context-term-init-plan/v1",
        "required_plugin": dict(REQUIRED_PLUGIN),
        "required_feature": REQUIRED_FEATURE,
        "core_repository_state": core_state,
        "active_core_entrypoint": None if preflight is None else preflight["observed"].get("entrypoint"),
        "owner_descriptor": descriptor,
        "descriptor_digest": canonical_digest(descriptor),
        "index_seed": seed,
        "index_seed_sha256": file_digest(seed),
        "bootstrap": {"owner": "context-core", "operation": "bootstrap", "host": host or "active_host", "area_register": "context-term", "index_path": TERM_INDEX},
        "applied": False,
    }


def _load_json_argument(value: str, *, allow_stdin: bool = False) -> Any:
    if value == "@-":
        if not allow_stdin:
            raise TermError("usage_invalid", "stdin is not allowed")
        text = sys.stdin.read()
    elif value.startswith("@"):
        try:
            text = pathlib.Path(value[1:]).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise TermError("input_unavailable", "JSON input could not be read", {"path": value[1:]}, EXIT_NOT_FOUND) from error
    else:
        raise TermError("usage_invalid", "JSON input must use @file or @-")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise TermError("schema_invalid", "input is not valid JSON") from error


def validate_core_doctor(doctor: Any) -> dict[str, Any]:
    if isinstance(doctor, dict) and "ok" in doctor:
        if set(doctor) != {"ok", "result"} or doctor.get("ok") is not True or not isinstance(doctor.get("result"), dict):
            raise TermError("core_preflight_invalid", "core doctor public envelope is invalid", exit_code=EXIT_CONFLICT)
        doctor = doctor["result"]
    required = {
        "schema", "owner", "supported_protocols", "repository_state", "root", "issues", "warnings",
        "plugin_version", "entrypoint", "protocol",
    }
    if not isinstance(doctor, dict) or set(doctor) != required:
        raise TermError("core_preflight_invalid", "core doctor fields differ from context-core-doctor/v1", exit_code=EXIT_CONFLICT)
    protocols = doctor.get("supported_protocols")
    issues = doctor.get("issues")
    warnings = doctor.get("warnings")
    entrypoint = doctor.get("entrypoint")
    try:
        resolved_entrypoint = required_core_surface(entrypoint) if isinstance(entrypoint, str) else None
        surface_version = core_surface_version(resolved_entrypoint) if resolved_entrypoint is not None else None
    except (TermError, OSError, RuntimeError):
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
        raise TermError("core_preflight_invalid", "core doctor identity or field shape is invalid", exit_code=EXIT_CONFLICT)
    for label, diagnostics in (("issues", issues), ("warnings", warnings)):
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("code"), str)
            or not item["code"]
            or any(not isinstance(key, str) for key in item)
            for item in diagnostics
        ):
            raise TermError("core_preflight_invalid", f"core doctor {label} diagnostics are invalid", exit_code=EXIT_CONFLICT)
    if doctor["repository_state"] == "ready" and issues:
        raise TermError("core_preflight_invalid", "ready core doctor must have no issues", exit_code=EXIT_CONFLICT)
    return doctor


def classify_core_preflight(inventory: Any, doctor: Any) -> dict[str, Any]:
    if not isinstance(inventory, dict) or not isinstance(inventory.get("plugins"), list):
        raise TermError("core_preflight_invalid", "core inventory or doctor receipt is invalid", exit_code=EXIT_CONFLICT)
    doctor = validate_core_doctor(doctor)
    plugins = [item for item in inventory["plugins"] if isinstance(item, dict) and item.get("plugin") == REQUIRED_PLUGIN["plugin"]]
    exact = [item for item in plugins if item.get("marketplace") == REQUIRED_PLUGIN["marketplace"]]
    if len(exact) > 1:
        raise TermError("core_preflight_invalid", "exact context-core inventory coordinate is ambiguous", exit_code=EXIT_CONFLICT)
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
        raise TermError("core_preflight_invalid", "repository_state is invalid", exit_code=EXIT_CONFLICT)
    return {"code": code, "host": None, "observed": observed}


def require_core_preflight(args: argparse.Namespace, *, allow_absent: bool = False, allow_partial: bool = False) -> dict[str, Any]:
    if not args.host or not args.core_inventory or not args.core_doctor:
        raise TermError("core_preflight_required", "non-static TERM operations require host inventory and core doctor receipt", {"required_plugin": REQUIRED_PLUGIN, "write_policy": {"repository": "none", "host_configuration": "none"}}, EXIT_CONFLICT)
    result = classify_core_preflight(_load_json_argument(args.core_inventory), _load_json_argument(args.core_doctor))
    result["host"] = args.host
    allowed = result["code"] == "ready" or (allow_absent and result["code"] == "core_uninitialized") or (allow_partial and result["code"] == "core_partial")
    if not allowed:
        raise TermError(result["code"], "exact context-core preflight failed", {"observed": result["observed"], "required_plugin": REQUIRED_PLUGIN, "write_policy": {"repository": "none", "host_configuration": "none"}}, EXIT_CONFLICT)
    return result


def _bobbin_settings(method, *args):
    path = pathlib.Path(__file__).resolve().parents[2] / "context/scripts/bobbin_config.py"
    spec = importlib.util.spec_from_file_location("bobbin_term_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return getattr(module, method)(*args)
    except module.ConfigError as error:
        raise TermError(error.code, str(error), exit_code=EXIT_CONFLICT) from error


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
        raise TermError("vault_not_found", "vault must be an existing directory", exit_code=EXIT_NOT_FOUND) from error


def _safe_term_path(
    repo: pathlib.Path,
    relative: str,
    *,
    state: str | None = None,
    index: bool = False,
) -> pathlib.Path:
    if not isinstance(relative, str):
        raise TermError("path_escape", "term path must be a string", exit_code=EXIT_CONFLICT)
    pure = pathlib.PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise TermError("path_escape", "path must be canonical repository-relative POSIX", {"path": relative}, EXIT_CONFLICT)
    if index:
        valid_shape = relative == TERM_INDEX
    elif state == "current":
        valid_shape = len(pure.parts) == 3 and pure.parts[:2] == ("context", "term") and pure.suffix == ".md" and not pure.name.endswith(".index.md")
    elif state == "history":
        valid_shape = len(pure.parts) == 4 and pure.parts[:3] == ("context", "term", "retired") and pure.suffix == ".md" and not pure.name.endswith(".index.md")
    else:
        valid_shape = (
            len(pure.parts) == 3
            and pure.parts[:2] == ("context", "term")
            and pure.suffix == ".md"
            and not pure.name.endswith(".index.md")
        ) or (
            len(pure.parts) == 4
            and pure.parts[:3] == ("context", "term", "retired")
            and pure.suffix == ".md"
            and not pure.name.endswith(".index.md")
        )
    if not valid_shape:
        raise TermError("path_escape", "path escapes the exact term artifact layout", {"path": relative}, EXIT_CONFLICT)
    repo_real = repo.resolve()
    current = repo_real
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise TermError("symlink_path", "symlink path components are not readable TERM artifacts", {"path": relative}, EXIT_CONFLICT)
    target = repo_real.joinpath(*pure.parts)
    area = repo_real / "context" / "term"
    try:
        target.resolve(strict=False).relative_to(area.resolve(strict=False))
    except ValueError as error:
        raise TermError("path_escape", "resolved path escapes context/term", {"path": relative}, EXIT_CONFLICT) from error
    return target


def _extract_block(text: str, block: str) -> list[str]:
    begin = f"<!-- BEGIN CONTEXT GENERATED:{block} -->"
    end = f"<!-- END CONTEXT GENERATED:{block} -->"
    if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) > text.index(end):
        raise TermError("index_stale", "term index block is malformed", {"block": block}, EXIT_INTEGRITY)
    return [line for line in text.split(begin, 1)[1].split(end, 1)[0].strip("\n").split("\n") if line]


def parse_term_index(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if 'area: "term"' not in text or 'owner: "context-term"' not in text or canonical_json(owner_descriptor()) not in _extract_block(text, "owner-profile"):
        raise TermError("index_stale", "term index descriptor/profile is invalid", exit_code=EXIT_INTEGRITY)
    output: list[list[dict[str, Any]]] = []
    for block, state in (("current", "current"), ("history", "history")):
        rows: list[dict[str, Any]] = []
        for line in _extract_block(text, block):
            match = ENTRY_RE.fullmatch(line)
            if not match:
                raise TermError("index_stale", "term index row is malformed", exit_code=EXIT_INTEGRITY)
            try:
                row = json.loads(match.group(1))
            except json.JSONDecodeError as error:
                raise TermError("index_stale", "term index row JSON is malformed", exit_code=EXIT_INTEGRITY) from error
            if row.get("state") != state or not _valid_context_id(row.get("id")) or not isinstance(row.get("path"), str):
                raise TermError("index_stale", "term index row identity/state is invalid", exit_code=EXIT_INTEGRITY)
            rows.append(row)
        output.append(rows)
    return output[0], output[1]


def _index(repo: pathlib.Path) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    path = _safe_term_path(repo, TERM_INDEX, index=True)
    if not path.is_file():
        raise TermError("term_area_missing", "term area index is missing", {"path": TERM_INDEX}, EXIT_NOT_FOUND)
    text = path.read_text(encoding="utf-8")
    current, history = parse_term_index(text)
    for row in current:
        _safe_term_path(repo, row["path"], state="current")
    for row in history:
        _safe_term_path(repo, row["path"], state="history")
    return text, current, history


def _record(repo: pathlib.Path, row: dict[str, Any]) -> dict[str, Any]:
    path = _safe_term_path(repo, row.get("path"), state=row.get("state"))
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise TermError("index_stale", "selected term path is missing", {"path": row["path"]}, EXIT_INTEGRITY) from error
    frontmatter, sections = parse_document(raw.decode("utf-8"))
    if frontmatter["id"] != row["id"]:
        raise TermError("index_stale", "selected term id differs from index", {"path": row["path"]}, EXIT_INTEGRITY)
    return {"path": row["path"], "state": row["state"], "frontmatter": frontmatter, "sections": sections, "sha256": bytes_digest(raw)}


def _current_record(repo: pathlib.Path, identifier: str) -> dict[str, Any]:
    require_context_id(identifier)
    _, current, _ = _index(repo)
    matches = [row for row in current if row["id"] == identifier]
    if len(matches) != 1:
        raise TermError("artifact_not_found", "Current term id was not found exactly once", {"id": identifier}, EXIT_NOT_FOUND)
    return _record(repo, matches[0])


def _history_path(path: str, identifier: str) -> str:
    source = pathlib.PurePosixPath(path)
    return f"context/term/retired/{source.stem}--{identifier[4:16]}.md"


def _mutation_request(transition: str, record: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "context-domain-mutation-input/v1",
        "transition": transition,
        "owner": "context-term",
        "target_kind": "term",
        "requested_changes": changes,
        "targets": [{"id": record["frontmatter"]["id"], "path": record["path"], "sha256": record["sha256"]}],
    }


def _single_mutation_result(record: dict[str, Any], transition: str, frontmatter: dict[str, Any], sections: dict[str, str], request: dict[str, Any], *, retire: bool) -> dict[str, Any]:
    effect_id = "effect_retire_term" if retire else "effect_replace_term"
    path = _history_path(record["path"], frontmatter["id"]) if retire else record["path"]
    content = render_document(frontmatter, sections)
    result = {
        "schema": "context-owner-result/v1", "result_type": "mutation", "transition": transition,
        "owner": "context-term", "target_kind": "term", "capability_digest": canonical_digest(term_capability()),
        "semantic_inputs": [_semantic_input("mutation_request", request)], "semantic_attestations": [],
        "artifact_drafts": [{"effect_id": effect_id, "path": path, "content": content, "semantic_projection": {"kind": "term", "primary_claim": _section_value(sections), "supporting_context": [frontmatter["term"]]} }],
        "effects": [{"effect_id": effect_id, "action": "retire" if retire else "replace", "area": "term", "id": frontmatter["id"], "state": "history" if retire else "current"}],
        "proposed_plan": {"schema": "context-owner-plan/v1", "transition": transition, "read_preconditions": [{"id": frontmatter["id"], "path": record["path"], "sha256": record["sha256"]}], "operations": [{"op": "move" if retire else "replace", "effect_id": effect_id, "area": "term", "from_path": record["path"], "to_path": path} if retire else {"op": "replace", "effect_id": effect_id, "area": "term", "path": path}]},
    }
    validate_owner_result(result)
    return result


def build_deprecate_result(
    repo: pathlib.Path,
    identifier: str,
    reason: str,
    replacement_term: str | None = None,
    *,
    retired_at: str | None = None,
) -> dict[str, Any]:
    record = _current_record(repo, identifier)
    reason = _substantive(reason, maximum=800, field="reason")
    replacement = None if replacement_term is None else _substantive(replacement_term, maximum=120, field="replacement_term")
    if replacement is not None and canonical_term_key(replacement) == record["frontmatter"]["term_key"]:
        raise TermError("lifecycle_invalid", "replacement term must occupy a different canonical slot", exit_code=EXIT_CONFLICT)
    frontmatter = dict(record["frontmatter"])
    frontmatter.update({
        "retired_at": _timestamp(retired_at or now_rfc3339(), "retired_at"),
        "retired_reason": "deprecated",
        "deprecation_reason": reason,
    })
    if replacement is not None:
        frontmatter["replacement_term"] = replacement
    request = _mutation_request("term_deprecate", record, {"reason": reason, "replacement_term": replacement})
    return _single_mutation_result(record, "term_deprecate", frontmatter, record["sections"], request, retire=True)


def prepare_same_claim_input(repo: pathlib.Path, identifier: str, successor_candidate: dict[str, Any]) -> dict[str, Any]:
    record = _current_record(repo, identifier)
    successor, _ = validate_term_candidate(successor_candidate)
    return {
        "schema": "context-term-same-claim-input/v1",
        "predecessor": {
            "id": identifier,
            "path": record["path"],
            "sha256": record["sha256"],
            "primary_claim": {"term": record["frontmatter"]["term"], "definition": _section_value(record["sections"])},
        },
        "successor": {
            "candidate_id": successor_candidate["candidate_id"],
            "primary_claim": {"term": successor["term"], "definition": successor["definition"]},
        },
    }


def build_supersede_result(
    repo: pathlib.Path,
    identifier: str,
    successor_candidate: dict[str, Any],
    claim_attestation: dict[str, Any],
    same_claim_input: dict[str, Any],
    same_claim_attestation: dict[str, Any],
    *,
    successor_id: str | None = None,
    retired_at: str | None = None,
) -> dict[str, Any]:
    record = _current_record(repo, identifier)
    expected_same = prepare_same_claim_input(repo, identifier, successor_candidate)
    if same_claim_input != expected_same:
        raise TermError("same_claim_input_invalid", "same_claim input must quote both actual primary claims", exit_code=EXIT_CONFLICT)
    validate_attestation(same_claim_attestation, same_claim_input, "same_claim", {"same_semantic_claim": ("/predecessor/primary_claim", "/successor/primary_claim")})
    successor_input, structural = validate_term_candidate(successor_candidate)
    validate_attestation(claim_attestation, successor_candidate, "claim", {"term_identified": ("/owner_inputs/term/term",), "definition_present": ("/owner_inputs/term/definition",)})
    if (structural["scope"], structural["term_key"]) != (record["frontmatter"]["scope"], record["frontmatter"]["term_key"]):
        raise TermError("successor_slot_mismatch", "successor term must retain the exact scope and term_key slot", exit_code=EXIT_CONFLICT)
    successor_id = require_context_id(successor_id or new_context_id(), "successor_id")
    at = _timestamp(retired_at or now_rfc3339(), "retired_at")
    old_frontmatter = dict(record["frontmatter"])
    old_frontmatter.update({"retired_at": at, "retired_reason": "superseded", "superseded_by": successor_id})
    new_frontmatter: dict[str, Any] = {
        "schema": "context-term/v1", "id": successor_id, "title": successor_candidate["title"], "summary": successor_candidate["summary"],
        "created_at": at, "captured_from": successor_candidate["captured_from"], "scope": structural["scope"],
        "term": successor_input["term"], "term_key": structural["term_key"], "supersedes": [identifier],
    }
    for field in ("tags", "search_terms", "source_refs"):
        if successor_candidate.get(field):
            new_frontmatter[field] = _string_list(successor_candidate[field], field, maximum=12, item_maximum=500 if field == "source_refs" else 40)
    for field in ("aliases", "deprecated_terms", "related"):
        if successor_input[field]:
            new_frontmatter[field] = successor_input[field]
    new_sections = _sections_from_input(successor_input)
    old_content = render_document(old_frontmatter, record["sections"])
    new_content = render_document(new_frontmatter, new_sections)
    old_effect, new_effect = "effect_retire_term", "effect_create_successor"
    old_path = _history_path(record["path"], identifier)
    new_path = "context/term/" + natural_filename(successor_candidate["title"])
    request = _mutation_request("term_supersede", record, {"successor_id": successor_id, "same_claim_input_digest": canonical_digest(same_claim_input)})
    result = {
        "schema": "context-owner-result/v1", "result_type": "mutation", "transition": "term_supersede", "owner": "context-term", "target_kind": "term", "capability_digest": canonical_digest(term_capability()),
        "semantic_inputs": [_semantic_input("claim", successor_candidate), _semantic_input("same_claim", same_claim_input), _semantic_input("mutation_request", request)],
        "semantic_attestations": [claim_attestation, same_claim_attestation],
        "artifact_drafts": [
            {"effect_id": old_effect, "path": old_path, "content": old_content, "semantic_projection": {"kind": "term", "primary_claim": _section_value(record["sections"]), "supporting_context": [record["frontmatter"]["term"]]}},
            {"effect_id": new_effect, "path": new_path, "content": new_content, "semantic_projection": {"kind": "term", "primary_claim": new_sections["Definition"], "supporting_context": [new_frontmatter["term"]]}},
        ],
        "effects": [
            {"effect_id": old_effect, "action": "retire", "area": "term", "id": identifier, "state": "history"},
            {"effect_id": new_effect, "action": "create", "area": "term", "id": successor_id, "state": "current"},
        ],
        "proposed_plan": {"schema": "context-owner-plan/v1", "transition": "term_supersede", "read_preconditions": [{"id": identifier, "path": record["path"], "sha256": record["sha256"]}], "operations": [
            {"op": "move", "effect_id": old_effect, "area": "term", "from_path": record["path"], "to_path": old_path},
            {"op": "create", "effect_id": new_effect, "area": "term", "path": new_path},
        ]},
    }
    validate_owner_result(result)
    return result


def build_annotate_result(repo: pathlib.Path, identifier: str, *, title: str | None = None, summary: str | None = None, tags: Sequence[str] | None = None, search_terms: Sequence[str] | None = None, source_refs: Sequence[str] | None = None, updated_at: str | None = None) -> dict[str, Any]:
    record = _current_record(repo, identifier)
    changes = {key: value for key, value in {"title": title, "summary": summary, "tags": list(tags) if tags is not None else None, "search_terms": list(search_terms) if search_terms is not None else None, "source_refs": list(source_refs) if source_refs is not None else None}.items() if value is not None}
    if not changes:
        raise TermError("usage_invalid", "annotate requires at least one metadata change")
    frontmatter = dict(record["frontmatter"])
    if title is not None:
        frontmatter["title"] = _substantive(title, maximum=120, field="title")
    if summary is not None:
        frontmatter["summary"] = _substantive(summary, maximum=280, field="summary")
    for field, value, item_maximum in (("tags", tags, 40), ("search_terms", search_terms, 40), ("source_refs", source_refs, 500)):
        if value is not None:
            frontmatter[field] = _string_list(list(value), field, maximum=12, item_maximum=item_maximum)
    frontmatter["updated_at"] = _timestamp(updated_at or now_rfc3339(), "updated_at")
    request = _mutation_request("term_annotate", record, changes)
    return _single_mutation_result(record, "term_annotate", frontmatter, record["sections"], request, retire=False)


def _require_signal(signal: str) -> None:
    if signal != SIGNAL:
        raise TermError("signal_required", "TERM search/read requires an exact encountered ambiguous or project-specific term signal", {"required": SIGNAL}, EXIT_CONFLICT)


def search_terms(repo: pathlib.Path, *, signal: str, query: str, include_history: bool = False, limit: int = 20) -> dict[str, Any]:
    _require_signal(signal)
    if not 1 <= limit <= 50:
        raise TermError("usage_invalid", "search limit must be in 1..50")
    _, current, history = _index(repo)
    needle = unicodedata.normalize("NFKC", query).casefold().strip()
    rows = current + (history if include_history else [])
    items = []
    for row in rows:
        haystack = " ".join([str(row.get("title", "")), str(row.get("summary", "")), str(row.get("scope", "")), *[str(value) for value in row.get("terms", [])]])
        if needle and needle not in unicodedata.normalize("NFKC", haystack).casefold():
            continue
        item = {key: row.get(key) for key in ("id", "path", "title", "summary", "scope", "term", "term_key", "state", "created_at")}
        item["authority"] = "authoritative" if row["state"] == "current" else "historical"
        item["do_not_follow"] = row["state"] == "history"
        items.append(item)
    items.sort(key=lambda item: (item.get("created_at", ""), item["id"]), reverse=True)
    return {"schema": "context-term-search/v1", "items": items[:limit], "returned": min(len(items), limit), "metadata_only": True, "signal": signal, "physical_write": False}


def read_term(repo: pathlib.Path, *, signal: str, identifier: str) -> dict[str, Any]:
    _require_signal(signal)
    require_context_id(identifier)
    _, current, history = _index(repo)
    rows = [row for row in current + history if row["id"] == identifier]
    if len(rows) != 1:
        raise TermError("artifact_not_found", "term id was not found exactly once", {"id": identifier}, EXIT_NOT_FOUND)
    record = _record(repo, rows[0])
    sections = {_canonical_section_name(name): value for name, value in record["sections"].items()}
    return {"schema": "context-term-read/v1", "id": identifier, "path": record["path"], "state": record["state"], "authority": "authoritative" if record["state"] == "current" else "historical", "do_not_follow": record["state"] == "history", "frontmatter": record["frontmatter"], "sections": sections, "sha256": record["sha256"], "signal": signal, "physical_write": False}


def _input_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["operation"]: item for item in result["semantic_inputs"]}


def _attestation_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["operation"]: item for item in result["semantic_attestations"]}


def _drafts_by_state(result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current = [draft for draft in result["artifact_drafts"] if "/retired/" not in draft["path"]]
    history = [draft for draft in result["artifact_drafts"] if "/retired/" in draft["path"]]
    return current, history


def _exact_mutation_source(repo: pathlib.Path, result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    request_item = _input_map(result).get("mutation_request")
    if request_item is None:
        raise TermError("semantic_input_invalid", "mutation request is missing", exit_code=EXIT_CONFLICT)
    request = request_item["value"]
    if (
        not isinstance(request, dict)
        or set(request) != {"schema", "transition", "owner", "target_kind", "requested_changes", "targets"}
        or request.get("schema") != "context-domain-mutation-input/v1"
        or request.get("transition") != result["transition"]
        or request.get("owner") != "context-term"
        or request.get("target_kind") != "term"
        or not isinstance(request.get("requested_changes"), dict)
        or not isinstance(request.get("targets"), list)
        or len(request["targets"]) != 1
    ):
        raise TermError("mutation_request_invalid", "mutation request envelope is not exact", exit_code=EXIT_CONFLICT)
    target = request["targets"][0]
    if not isinstance(target, dict) or set(target) != {"id", "path", "sha256"}:
        raise TermError("mutation_request_invalid", "mutation target fields are not exact", exit_code=EXIT_CONFLICT)
    record = _current_record(repo, target.get("id"))
    expected_target = {"id": record["frontmatter"]["id"], "path": record["path"], "sha256": record["sha256"]}
    if target != expected_target:
        raise TermError("source_precondition_mismatch", "mutation target differs from the live Current artifact", {"expected": expected_target, "actual": target}, EXIT_CONFLICT)
    return request, record


def _rebuild_owner_result(repo: pathlib.Path, owner_result: dict[str, Any]) -> dict[str, Any]:
    """Re-derive the exact result from live bytes and bound semantic inputs."""

    validate_owner_result(owner_result)
    inputs = _input_map(owner_result)
    attestations = _attestation_map(owner_result)
    transition = owner_result["transition"]
    current_drafts, history_drafts = _drafts_by_state(owner_result)
    if transition == "capture":
        if len(current_drafts) != 1 or history_drafts:
            raise TermError("owner_result_invalid", "receipt validation requires one complete capture draft", exit_code=EXIT_CONFLICT)
        frontmatter, _ = parse_document(current_drafts[0]["content"])
        expected = build_claim_result(
            inputs["claim"]["value"],
            attestations["claim"],
            identifier=frontmatter["id"],
            created_at=frontmatter["created_at"],
            filename=pathlib.PurePosixPath(current_drafts[0]["path"]).name,
        )
    else:
        request, record = _exact_mutation_source(repo, owner_result)
        changes = request["requested_changes"]
        if transition == "term_deprecate":
            if set(changes) != {"reason", "replacement_term"} or len(history_drafts) != 1 or current_drafts:
                raise TermError("mutation_request_invalid", "deprecate request or draft topology differs", exit_code=EXIT_CONFLICT)
            retired, _ = parse_document(history_drafts[0]["content"])
            expected = build_deprecate_result(
                repo,
                record["frontmatter"]["id"],
                changes.get("reason"),
                changes.get("replacement_term"),
                retired_at=retired["retired_at"],
            )
        elif transition == "term_annotate":
            allowed = {"title", "summary", "tags", "search_terms", "source_refs"}
            if not changes or set(changes) - allowed or len(current_drafts) != 1 or history_drafts:
                raise TermError("mutation_request_invalid", "annotate request contains semantic or unknown changes", exit_code=EXIT_CONFLICT)
            annotated, _ = parse_document(current_drafts[0]["content"])
            expected = build_annotate_result(
                repo,
                record["frontmatter"]["id"],
                title=changes.get("title"),
                summary=changes.get("summary"),
                tags=changes.get("tags") if "tags" in changes else None,
                search_terms=changes.get("search_terms") if "search_terms" in changes else None,
                source_refs=changes.get("source_refs") if "source_refs" in changes else None,
                updated_at=annotated["updated_at"],
            )
        elif transition == "term_supersede":
            if set(changes) != {"successor_id", "same_claim_input_digest"} or len(current_drafts) != 1 or len(history_drafts) != 1:
                raise TermError("mutation_request_invalid", "supersede request or topology differs", exit_code=EXIT_CONFLICT)
            successor, _ = parse_document(current_drafts[0]["content"])
            predecessor, _ = parse_document(history_drafts[0]["content"])
            same_claim = inputs.get("same_claim", {}).get("value")
            if changes.get("same_claim_input_digest") != canonical_digest(same_claim) or changes.get("successor_id") != successor["id"]:
                raise TermError("same_claim_input_invalid", "supersede request is detached from same_claim or successor", exit_code=EXIT_CONFLICT)
            expected = build_supersede_result(
                repo,
                record["frontmatter"]["id"],
                inputs["claim"]["value"],
                attestations["claim"],
                same_claim,
                attestations["same_claim"],
                successor_id=successor["id"],
                retired_at=predecessor["retired_at"],
            )
        else:
            raise TermError("transition_topology_invalid", "TERM transition is unsupported", exit_code=EXIT_CONFLICT)
    if expected != owner_result:
        raise TermError(
            "owner_result_rederivation_mismatch",
            "owner result differs from live source, exact candidate/request, or derived artifact bytes",
            {"transition": transition},
            EXIT_CONFLICT,
        )
    return expected


def _validate_prior_chain(prior_bundles: Sequence[dict[str, Any]]) -> tuple[list[str], list[str]]:
    all_digests: list[str] = []
    same_area: list[str] = []
    for bundle in prior_bundles:
        if not isinstance(bundle, dict) or bundle.get("schema") != "context-mutation-bundle/v1" or bundle.get("approval_digest") != canonical_digest(bundle.get("approval_material")):
            raise TermError("prior_bundle_invalid", "prior bundle digest is invalid", exit_code=EXIT_CONFLICT)
        plan = bundle.get("approval_material", {}).get("plan")
        if not isinstance(plan, dict) or plan.get("prior_bundle_digests") != all_digests:
            raise TermError("prior_bundle_order_invalid", "prior bundle chain differs from exact proposal order", exit_code=EXIT_CONFLICT)
        digest = bundle["approval_digest"]
        all_digests.append(digest)
        if plan.get("owner") == "context-term" or plan.get("primary_area") == "term":
            same_area.append(digest)
    if same_area:
        raise TermError(
            "prior_same_area_requires_apply",
            "TERM receipt validation does not trust unapplied same-area virtual state",
            {"prior_same_area_bundle_digests": same_area},
            EXIT_CONFLICT,
        )
    return all_digests, same_area


def _validate_term_slots(repo: pathlib.Path, owner_result: dict[str, Any]) -> None:
    """Validate every Current vocabulary key across overlapping scopes from actual bodies."""

    _, current_rows, _ = _index(repo)
    current = [_record(repo, row)["frontmatter"] for row in current_rows]
    transition = owner_result["transition"]
    drafts = [
        parse_document(draft["content"])[0]
        for draft in owner_result["artifact_drafts"]
        if "/retired/" not in draft["path"]
    ]
    target_ids = {
        target["id"]
        for item in owner_result["semantic_inputs"]
        if item["operation"] == "mutation_request"
        for target in item["value"].get("targets", [])
    }
    if transition in {"term_annotate", "term_supersede", "term_deprecate"}:
        current = [frontmatter for frontmatter in current if frontmatter["id"] not in target_ids]
    if transition != "term_deprecate":
        current.extend(drafts)
    conflicts: list[dict[str, str]] = []
    for index, left in enumerate(current):
        for right in current[index + 1:]:
            if not scopes_overlap(left["scope"], right["scope"]):
                continue
            left_keys = {
                canonical_term_key(value): field
                for field, values in (
                    ("term", [left["term"]]),
                    ("aliases", left.get("aliases", [])),
                    ("deprecated_terms", left.get("deprecated_terms", [])),
                )
                for value in values
            }
            right_keys = {
                canonical_term_key(value): field
                for field, values in (
                    ("term", [right["term"]]),
                    ("aliases", right.get("aliases", [])),
                    ("deprecated_terms", right.get("deprecated_terms", [])),
                )
                for value in values
            }
            for key in sorted(set(left_keys) & set(right_keys)):
                conflicts.append({
                    "left": left["id"],
                    "right": right["id"],
                    "term_key": key,
                    "left_field": left_keys[key],
                    "right_field": right_keys[key],
                })
    if conflicts:
        raise TermError(
            "term_slot_conflict",
            "Current term, alias, or deprecated term overlaps an exact, ancestor, or descendant scope",
            {"conflicts": conflicts},
            EXIT_CONFLICT,
        )


def validate_batch(repo: pathlib.Path, owner_result: dict[str, Any], prior_bundles: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    _rebuild_owner_result(repo, owner_result)
    _validate_term_slots(repo, owner_result)
    index_text, _, _ = _index(repo)
    _, same_area = _validate_prior_chain(prior_bundles)
    topology = {
        "capture": "create_current",
        "term_annotate": "replace_same_state",
        "term_deprecate": "retire_current",
        "term_supersede": "supersede_current",
    }.get(owner_result["transition"])
    if topology is None:
        raise TermError("transition_topology_invalid", "TERM transition has no generic topology", exit_code=EXIT_CONFLICT)
    receipt = {
        "schema": "context-owner-validation-receipt/v2", "owner": "context-term", "kind": "term",
        "descriptor_digest": canonical_digest(owner_descriptor()), "capability": term_capability(),
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
    parser = argparse.ArgumentParser(prog="term_cli.py")
    parser.add_argument("--vault", help="Directory containing context/; defaults to the nearest context/ ancestor or cwd.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("schema", "capabilities"):
        command = sub.add_parser(name)
        command.add_argument("--json", action="store_true")
    init = sub.add_parser("init")
    init.add_argument("--json", action="store_true")
    claim = sub.add_parser("claim")
    claim.add_argument("--candidate", required=True, metavar="@FILE", help="structured candidate JSON input via @file")
    claim.add_argument("--attestation", required=True)
    claim.add_argument("--identifier")
    claim.add_argument("--created-at")
    claim.add_argument("--filename")
    claim.add_argument("--route-only", action="store_true")
    claim.add_argument("--json", action="store_true")
    decline = sub.add_parser("decline")
    decline.add_argument("--candidate", required=True, metavar="@FILE", help="structured candidate JSON input via @file")
    decline.add_argument("--reason", required=True)
    decline.add_argument("--json", action="store_true")
    candidate_batch = sub.add_parser("candidate-batch")
    candidate_batch_sub = candidate_batch.add_subparsers(dest="candidate_batch_command", required=True)
    candidate_batch_validate = candidate_batch_sub.add_parser("validate")
    candidate_batch_validate.add_argument("--batch", required=True)
    candidate_batch_validate.add_argument("--json", action="store_true")
    search = sub.add_parser("search")
    search.add_argument("--signal", required=True)
    search.add_argument("--query", default="")
    search.add_argument("--include-history", action="store_true")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")
    read = sub.add_parser("read")
    read.add_argument("--signal", required=True)
    read.add_argument("--id", required=True)
    read.add_argument("--json", action="store_true")
    deprecate = sub.add_parser("deprecate")
    deprecate.add_argument("--id", required=True)
    deprecate.add_argument("--reason", required=True)
    deprecate.add_argument("--replacement-term")
    deprecate.add_argument("--retired-at")
    deprecate.add_argument("--json", action="store_true")
    same = sub.add_parser("same-claim-input")
    same.add_argument("--id", required=True)
    same.add_argument("--successor-candidate", required=True, metavar="@FILE", help="structured successor candidate JSON input via @file")
    same.add_argument("--json", action="store_true")
    supersede = sub.add_parser("supersede")
    supersede.add_argument("--id", required=True)
    supersede.add_argument("--successor-candidate", required=True, metavar="@FILE", help="structured successor candidate JSON input via @file")
    supersede.add_argument("--claim-attestation", required=True)
    supersede.add_argument("--same-claim-input", required=True)
    supersede.add_argument("--same-claim-attestation", required=True)
    supersede.add_argument("--successor-id")
    supersede.add_argument("--retired-at")
    supersede.add_argument("--json", action="store_true")
    annotate = sub.add_parser("annotate")
    annotate.add_argument("--id", required=True)
    annotate.add_argument("--title")
    annotate.add_argument("--summary")
    annotate.add_argument("--tag", action="append")
    annotate.add_argument("--search-term", action="append")
    annotate.add_argument("--source-ref", action="append")
    annotate.add_argument("--updated-at")
    annotate.add_argument("--json", action="store_true")
    batch = sub.add_parser("batch")
    batch_sub = batch.add_subparsers(dest="batch_command", required=True)
    validate = batch_sub.add_parser("validate")
    validate.add_argument("--owner-result", required=True)
    validate.add_argument("--prior-bundle", action="append", default=[])
    validate.add_argument("--json", action="store_true")
    for command in (init, claim, decline, candidate_batch_validate, search, read, deprecate, same, supersede, annotate, validate):
        _add_preflight(command)
    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "schema":
        return schema_result()
    if args.command == "capabilities":
        return {"schema": "context-owner-capabilities/v1", "owners": [term_capability()]}
    preflight = require_core_preflight(args, allow_absent=args.command == "init", allow_partial=args.command == "init")
    if args.command == "init":
        return build_init_plan(preflight)
    if args.command == "claim":
        return build_claim_result(_load_json_argument(args.candidate, allow_stdin=True), _load_json_argument(args.attestation), identifier=args.identifier, created_at=args.created_at, filename=args.filename, route_only=args.route_only)
    if args.command == "decline":
        return build_decline_result(_load_json_argument(args.candidate, allow_stdin=True), args.reason)
    if args.command == "candidate-batch" and args.candidate_batch_command == "validate":
        return validate_candidate_batch(_load_json_argument(args.batch, allow_stdin=True))
    repo = vault_root(getattr(args, "vault", None))
    if args.command == "search":
        return search_terms(repo, signal=args.signal, query=args.query, include_history=args.include_history, limit=args.limit)
    if args.command == "read":
        return read_term(repo, signal=args.signal, identifier=args.id)
    if args.command == "deprecate":
        return build_deprecate_result(repo, args.id, args.reason, args.replacement_term, retired_at=args.retired_at)
    if args.command == "same-claim-input":
        return prepare_same_claim_input(repo, args.id, _load_json_argument(args.successor_candidate, allow_stdin=True))
    if args.command == "supersede":
        return build_supersede_result(repo, args.id, _load_json_argument(args.successor_candidate, allow_stdin=True), _load_json_argument(args.claim_attestation), _load_json_argument(args.same_claim_input), _load_json_argument(args.same_claim_attestation), successor_id=args.successor_id, retired_at=args.retired_at)
    if args.command == "annotate":
        return build_annotate_result(repo, args.id, title=args.title, summary=args.summary, tags=args.tag, search_terms=args.search_term, source_refs=args.source_ref, updated_at=args.updated_at)
    if args.command == "batch" and args.batch_command == "validate":
        return validate_batch(repo, _load_json_argument(args.owner_result, allow_stdin=True), [_load_json_argument(value) for value in args.prior_bundle])
    raise TermError("usage_invalid", "unsupported command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        with _bobbin_settings("project_environment"):
            result = dispatch(args)
        output = {"ok": True, "result": result} if getattr(args, "json", False) else result
        sys.stdout.write(_serialize_public(output))
        return 0
    except TermError as error:
        try:
            sys.stdout.write(_serialize_public(error.envelope()))
        except TermError:
            sys.stdout.write('{"error":{"code":"output_too_large","details":{"maximum":32768},"message":"public output exceeds the 32 KiB UTF-8 byte budget"},"ok":false}\n')
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
