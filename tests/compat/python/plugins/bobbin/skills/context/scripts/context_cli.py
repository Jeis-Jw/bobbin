#!/usr/bin/env python3
"""context-core v1 storage/index/coordinator kernel (Python 3.11+, stdlib only)."""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import stat
import sys
import tempfile
import unicodedata
import uuid
from typing import Any, Iterable, Iterator, Mapping, Sequence


EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CONFLICT = 5
EXIT_INTEGRITY = 6
PROTOCOL = "context-common/v2"
CORE_RECEIPT_SCHEMA = "context-core-workflow-receipt/v1"
CORE_RECEIPT_FIELDS = (
    "schema", "status", "created_at", "plan_id", "core", "plan_bundle", "receipt_digest",
)
MAX_STAGE1_BYTES = 4 * 1024
MAX_SECTION_ITEM_BYTES = 2 * 1024
MAX_RECALL_BATCH_BYTES = 8 * 1024
MAX_USER_BYTES = 32 * 1024
MAX_CANDIDATE_BYTES = 16 * 1024
MAX_OWNER_INPUT_BYTES = 8 * 1024
MAX_PRIMARY_CLAIM_CODEPOINTS = 2000
MAX_APPROVAL_PREVIEW_BYTES = 32 * 1024
MAX_ARCHIVE_CONTENT_CODEPOINTS = 65_000
MAX_ARCHIVE_INPUT_BYTES = MAX_ARCHIVE_CONTENT_CODEPOINTS * 4
MAX_ARCHIVE_ENVELOPE_BYTES = 512 * 1024
MAX_INDEX_FALLBACK_ARTIFACT_READS = 20
MAX_OWNER_DESCRIPTOR_BYTES = 8 * 1024
MAX_PROFILE_FIELDS = 24
MAX_PROFILE_ITEMS = 12
ROOT_INDEX = "context/context.index.md"
BUILTIN_AREAS = ("snapshot", "observation", "archive")
RESERVED_INDEX_PATHS = {
    ROOT_INDEX,
    "context/snapshot/snapshot.index.md",
    "context/observation/observation.index.md",
    "context/archive/archive.index.md",
    "context/decision/decision.index.md",
}
INDEX_FIXABLE_CODES = {
    "index_content_drift",
    "index_duplicate_entry",
    "index_ghost_entry",
    "index_missing_entry",
    "index_self_entry",
    "index_wrong_state",
}
OWNER_RESULT_FIELDS = {
    "schema", "result_type", "transition", "owner", "target_kind", "candidate_id", "decision", "reason",
    "capability_digest", "semantic_inputs", "semantic_attestations", "artifact_drafts", "effects", "proposed_plan",
}
COMMON_KEY_ORDER = (
    "schema", "id", "title", "summary", "created_at", "updated_at", "captured_from", "source_refs", "tags",
    "search_terms",
)
ADDITIVE_KEY_ORDER = {
    "context-snapshot/v1": ("anchors",),
    "context-observation/v1": (
        "kind_hint", "verified_at", "affects_paths", "relations", "supersedes",
        "superseded_by", "retired_at", "retired_reason", "retirement_note",
    ),
    "context-archive/v1": (),
    "context-decision/v1": (
        "scope", "decision_key", "revisit_when", "revisit_on", "relations", "supersedes", "superseded_by",
        "retired_at", "retired_reason", "retirement_note",
    ),
}
SECTION_SPECS = {
    "context-snapshot/v1": (("Current context", "Open items", "Next steps", "Decided", "References", "Capture candidates"), ("Current context", "Open items", "Next steps")),
    "context-observation/v1": (("Observation", "Evidence", "Impact", "Current handling", "Follow-up conditions"), ("Observation", "Evidence")),
    "context-archive/v1": (("Content",), ("Content",)),
    "context-decision/v1": (("Decision", "Rationale", "Rejected alternatives", "Evidence and constraints", "Trade-offs", "Revisit conditions"), ("Decision", "Rationale", "Rejected alternatives")),
}
LEGACY_SECTION_ALIASES = {
    "context-snapshot/v1": {
        "현재 맥락": "Current context", "열린 항목": "Open items", "다음 단계": "Next steps",
        "정해진 것": "Decided", "참조": "References", "capture 후보": "Capture candidates",
    },
    "context-observation/v1": {
        "관찰": "Observation", "근거": "Evidence", "영향": "Impact",
        "현재 처리": "Current handling", "후속 조건": "Follow-up conditions",
    },
    "context-decision/v1": {
        "결정": "Decision", "취지": "Rationale", "반려대안": "Rejected alternatives",
        "근거와 제약": "Evidence and constraints", "트레이드오프": "Trade-offs", "재평가 조건": "Revisit conditions",
    },
    "context-assumption/v1": {
        "가정": "Assumption", "근거": "Basis", "확정 조건": "Confirmation conditions", "반증 조건": "Refutation conditions",
    },
    "context-term/v1": {"정의": "Definition"},
}
PLACEHOLDERS = {"...", "TODO", "TBD", "N/A", "해당 없음"}
FILENAME_FORBIDDEN = set('/\\<>:"|?*[]#^')
WINDOWS_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.IGNORECASE)
FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
LOCAL_ID = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
AREA_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
TYPED_RELATION_KEY = re.compile(r"^(?P<predicate>[a-z][a-z0-9_]{0,79}):(?P<target_kind>[a-z][a-z0-9_-]{0,79})$")
ROOT_ROW = re.compile(r"^.*<!-- context-area (\{.*\}) -->$")
ENTRY_ROW = re.compile(r"^.*<!-- context-entry (\{.*\}) -->$")
OWNER_PROFILE_ROW = re.compile(r"^<!-- context-owner-profile (\{.*\}) -->$")
PROFILE_FIELD_TYPES = {
    "string", "string_list", "date", "timestamp", "enum", "context_id", "context_id_list", "relation_map",
}
PROFILE_TOPOLOGIES = {
    "create_current", "replace_same_state", "retire_current", "supersede_current", "delete_one",
}
PROFILE_COMMON_FIELDS = set(COMMON_KEY_ORDER) | {"id"}
POLICY_BEGIN = "<!-- BEGIN context-core-policy (managed by context-core) -->"
POLICY_END = "<!-- END context-core-policy (managed by context-core) -->"
# POLICY_BODY is user-facing operating guidance. The transport-level frozen-bundle,
# approval_digest, repository-identity, CAS, lock, and atomic-write checks remain
# enforced by build/apply code below and are intentionally not delegated to users.
POLICY_BODY = """<!-- BEGIN context-core-policy (managed by context-core) -->
## Durable context workflow

- Resolve the active language from an explicit user language choice, then the host's preferred response language, then the established conversation language, and finally English. OS locale is not authoritative. Keep machine-readable surfaces in canonical English and preserve artifact prose.
- Bobbin is one plugin. On a durable signal, read project-local `.bobbin/config.json` for enabled features and approval mode. Missing config preserves legacy registered features and explicit mode. Shared vaults do not share settings. Only a direct user request through Bobbin init may change settings.
- Audit each user turn's new meaning once. No durable signal means zero context tool calls and no audit status or capture question. For a mechanical local edit, skip AGENTS/guidance discovery and exclude `context/`: use the named path, or infer one task subtree and search once. Never use `.`, `--hidden`, repository-wide globs or the repository root; ask for the path instead of widening. Keep the scope/anchor, read Current IDs and pending/dismissed references in a session-only ledger, never a copied vault.
- Recall metadata first only if prior context can change the answer, then read selected actual bodies. Let the semantic owner compare actual bodies, scope, and rationale. IDs and metadata are not semantic evidence. Report a conflict before the primary conclusion. Hold an action whose change intent is unresolved: keep means not performed; supersede requires an explicit user choice. A satisfied revisit condition permits reassessment, not implementation. Never ask again for an already explicit choice.
- Only enabled owners participate in automatic recall, routing and recording. Disabled features preserve records and explicit historical reads. SNAP is staging, OBS is evidence and DEC is an authoritative user choice. Do not turn uncertainty or an LLM preference into a user commitment.
- Finish the primary request first. Follow the loaded shared recording policy: `explicit` records on direct, explicit, unconditional semantic approval; `auto` records eligible durable context without per-record questions; `adaptive` lets the LLM choose record or ask based on meaning, scope, evidence, conflicts and consequences. Ask only when the selected mode requires it, never a second storage question.
- Core is the sole writer. Every mode uses internal preview and unchanged apply in the same response, with frozen payload, project/vault identity, CAS, lock and atomic-write checks. Never show the rendered storage body merely to authorize persistence. Never regenerate after approval; re-evaluate if settings or content change. A generic acknowledgement, praise, condition, edit request, or topic change is not user approval. Report policy-authorized records honestly. Recording policy grants no unrelated execution or publication permission.
<!-- END context-core-policy (managed by context-core) -->"""
POLICY_TARGETS = {"AGENTS.md", "CLAUDE.md"}
POLICY_HOST_TARGETS = {"codex": "AGENTS.md", "claude-code": "CLAUDE.md"}
MERGE_ATTRIBUTES_TARGET = ".gitattributes"
MERGE_ATTRIBUTES_BEGIN = "# BEGIN context-core-merge (managed by context-core)"
MERGE_ATTRIBUTES_END = "# END context-core-merge (managed by context-core)"
MERGE_ATTRIBUTES_BODY = """# BEGIN context-core-merge (managed by context-core)
# Generated context indexes are projections of the artifacts. Let Git keep both sides,
# then run `context_cli.py refresh --fix index` to re-derive the canonical index.
context/**/*.index.md merge=union
# END context-core-merge (managed by context-core)"""
_POLICY_SPEC = {
    "begin": POLICY_BEGIN, "end": POLICY_END, "body": POLICY_BODY, "transition": "policy_install",
    "effect_id": "effect_install_policy", "action": "install_policy", "material_id": "material_policy",
    "descriptor": {"owner": "context-core", "kind": "policy", "artifact_schema": "context-policy/v1"},
}
MANAGED_ROOT_FILES = {
    "AGENTS.md": _POLICY_SPEC,
    "CLAUDE.md": _POLICY_SPEC,
    MERGE_ATTRIBUTES_TARGET: {
        "begin": MERGE_ATTRIBUTES_BEGIN, "end": MERGE_ATTRIBUTES_END, "body": MERGE_ATTRIBUTES_BODY,
        "transition": "merge_attributes_install", "effect_id": "effect_install_merge_attributes",
        "action": "install_merge_attributes", "material_id": "material_merge_attributes",
        "descriptor": {"owner": "context-core", "kind": "merge_attributes", "artifact_schema": "context-merge-attributes/v1"},
    },
}
MANAGED_ROOT_TRANSITIONS = {spec["transition"] for spec in MANAGED_ROOT_FILES.values()}
REMOVED_FINGERPRINT_FIELDS = {"claim_fingerprint", "source_claim_fingerprint"}
REMOVED_CANDIDATE_FIELDS = REMOVED_FINGERPRINT_FIELDS | {"claim_key"}

_BOBBIN_CONFIG = None


def _config_call(method, *args, **kwargs):
    global _BOBBIN_CONFIG
    if _BOBBIN_CONFIG is None:
        path = pathlib.Path(__file__).with_name("bobbin_config.py")
        spec = importlib.util.spec_from_file_location("bobbin_config_runtime_" + str(id(sys.modules[__name__])), path)
        _BOBBIN_CONFIG = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_BOBBIN_CONFIG)
    try:
        return getattr(_BOBBIN_CONFIG, method)(*args, **kwargs)
    except _BOBBIN_CONFIG.ConfigError as error:
        raise ContextError(error.code, str(error), exit_code=EXIT_CONFLICT) from error


def project_settings(repo=None):
    return _config_call("load", repo)


class ContextError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, exit_code: int = EXIT_USAGE):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.exit_code = exit_code

    def envelope(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message, "details": self.details}}


@dataclasses.dataclass(frozen=True)
class Document:
    frontmatter: dict[str, Any]
    sections: dict[str, str]
    warnings: tuple[dict[str, Any], ...] = ()


@dataclasses.dataclass(frozen=True)
class AreaIndex:
    frontmatter: dict[str, Any]
    current: list[dict[str, Any]]
    history: list[dict[str, Any]]
    text: str
    # Set when the bytes on disk are a non-canonical projection (for example after a Git
    # union merge): ``text`` then holds the canonical index re-derived from the artifacts
    # and ``physical_text`` the bytes that were actually read.
    physical_text: str | None = None


def _index_base_digest(index: AreaIndex) -> str:
    return sha256_bytes((index.physical_text if index.physical_text is not None else index.text).encode("utf-8"))


def _tolerant_area_index(repo: pathlib.Path, area: str, index_text: str) -> AreaIndex:
    """Parse an area index; re-derive a non-canonical projection from the artifacts in memory.

    Only row-level drift (``index_noncanonical``: order, duplicates, stale JSON) is tolerated.
    Structural problems (schema, metadata, profile) still raise. Nothing is written here.
    """
    try:
        return parse_area_index(index_text)
    except ContextError as error:
        if error.code != "index_noncanonical":
            raise
    canonical = render_area_index_from_repository(repo, area, repair_rows=True)
    regenerated = parse_area_index(canonical)
    return AreaIndex(regenerated.frontmatter, regenerated.current, regenerated.history, canonical, index_text)


def _decision_scopes_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _git_metadata_root(repo: pathlib.Path) -> pathlib.Path | None:
    for candidate in (repo, *list(repo.parents)[:8]):
        if (candidate / ".git").exists():
            return candidate
    return None


def _merge_attributes_installed(repo: pathlib.Path) -> bool:
    try:
        text = (repo / MERGE_ATTRIBUTES_TARGET).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return MERGE_ATTRIBUTES_BEGIN in text and MERGE_ATTRIBUTES_END in text and "merge=union" in text


@dataclasses.dataclass
class IOMetrics:
    index_opens: int = 0
    index_read_bytes: int = 0
    artifact_opens: int = 0
    artifact_read_bytes: int = 0
    artifact_directory_lists: int = 0
    artifact_stats: int = 0
    output_bytes: int = 0


def _canonical_section_name(schema: str, name: str) -> str:
    return LEGACY_SECTION_ALIASES.get(schema, {}).get(name, name)


def _legacy_section_name(schema: str, canonical: str) -> str | None:
    return next(
        (legacy for legacy, target in LEGACY_SECTION_ALIASES.get(schema, {}).items() if target == canonical),
        None,
    )


def _section_value(sections: dict[str, str], schema: str, canonical: str) -> str | None:
    if canonical in sections:
        return sections[canonical]
    legacy = _legacy_section_name(schema, canonical)
    return sections.get(legacy) if legacy is not None else None


def _sections_in_existing_style(
    sections: dict[str, str],
    schema: str,
    existing: dict[str, str],
) -> dict[str, str]:
    legacy_style = any(name in LEGACY_SECTION_ALIASES.get(schema, {}) for name in existing)
    output: dict[str, str] = {}
    for name, value in sections.items():
        canonical = _canonical_section_name(schema, name)
        target = (_legacy_section_name(schema, canonical) or canonical) if legacy_style else canonical
        output[target] = value
    return output


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalized_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _canonical_slot_part(value: str, *, maximum: int) -> str:
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
    return result if result and len(result) <= maximum else ""


def _canonical_decision_scope(value: str) -> str:
    if not isinstance(value, str):
        return ""
    stripped = normalized_key(value.strip()).strip("/")
    if not stripped or "//" in stripped:
        return ""
    parts = stripped.split("/")
    if len(parts) > 8:
        return ""
    canonical = [_canonical_slot_part(part, maximum=40) for part in parts]
    result = "/".join(canonical)
    return result if all(canonical) and len(result) <= 160 else ""


def _canonical_decision_key(value: str) -> str:
    if not isinstance(value, str) or "/" in value:
        return ""
    return _canonical_slot_part(value, maximum=80)


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return nfc(value)
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= (2**53 - 1):
        return value
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise ContextError("canonical_json_invalid", "object keys must be strings")
            key = nfc(raw_key)
            if key in normalized:
                raise ContextError("canonical_json_invalid", "NFC-normalized object keys collide", {"key": key})
            normalized[key] = _canonical_value(raw_value)
        return {key: normalized[key] for key in sorted(normalized)}
    raise ContextError("canonical_json_invalid", "unsupported canonical JSON scalar", {"type": type(value).__name__})


def canonical_json(value: Any) -> str:
    return json.dumps(_canonical_value(value), ensure_ascii=False, separators=(",", ":"))


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _strict_json_loads(text: str, *, code: str = "schema_invalid") -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ContextError(code, "JSON object contains a duplicate key", {"key": key}, EXIT_CONFLICT)
            output[key] = value
        return output

    try:
        return json.loads(text, object_pairs_hook=object_pairs)
    except json.JSONDecodeError as error:
        raise ContextError(code, "input is not valid JSON", exit_code=EXIT_CONFLICT) from error


def _profile_error(message: str, details: dict[str, Any] | None = None) -> None:
    raise ContextError("owner_descriptor_invalid", message, details, EXIT_CONFLICT)


def _bounded_json_shape(value: Any, *, depth: int = 0, counter: list[int] | None = None) -> None:
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if depth > 8 or counter[0] > 192:
        _profile_error("owner descriptor exceeds the bounded structural depth or node count")
    if isinstance(value, dict):
        if len(value) > MAX_PROFILE_FIELDS:
            _profile_error("owner descriptor object exceeds the bounded field count")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80:
                _profile_error("owner descriptor key is invalid")
            _bounded_json_shape(item, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        if len(value) > MAX_PROFILE_ITEMS:
            _profile_error("owner descriptor array exceeds the bounded item count")
        for item in value:
            _bounded_json_shape(item, depth=depth + 1, counter=counter)
    elif not (value is None or isinstance(value, (str, bool)) or (isinstance(value, int) and not isinstance(value, bool))):
        _profile_error("owner descriptor contains an unsupported scalar type")


def _profile_string_list(value: Any, field: str, *, maximum: int = MAX_PROFILE_ITEMS) -> list[str]:
    if (
        not isinstance(value, list)
        or not 0 <= len(value) <= maximum
        or any(
            not isinstance(item, str)
            or not item
            or item != item.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in item)
            or len(item) > 80
            for item in value
        )
        or len(value) != len(set(value))
    ):
        _profile_error(f"{field} must be a bounded unique string list")
    return value


def _validate_profile_field(name: str, spec: Any) -> None:
    if not FIELD_KEY.fullmatch(name) or name in PROFILE_COMMON_FIELDS or name in REMOVED_FINGERPRINT_FIELDS:
        _profile_error("structural profile field name is invalid", {"field": name})
    if not isinstance(spec, dict) or spec.get("type") not in PROFILE_FIELD_TYPES or not isinstance(spec.get("required"), bool):
        _profile_error("structural profile field declaration is invalid", {"field": name})
    kind = spec["type"]
    expected: set[str]
    if kind == "string":
        expected = {"type", "required", "min_chars", "max_chars"}
        minimum, maximum = spec.get("min_chars"), spec.get("max_chars")
        if (
            not isinstance(minimum, int) or isinstance(minimum, bool)
            or not isinstance(maximum, int) or isinstance(maximum, bool)
            or not 0 <= minimum <= maximum <= 4000
            or (spec["required"] and minimum < 1)
        ):
            _profile_error("string field bounds are invalid", {"field": name})
    elif kind == "string_list":
        expected = {"type", "required", "min_items", "max_items", "max_item_chars"}
        minimum, maximum, item_maximum = spec.get("min_items"), spec.get("max_items"), spec.get("max_item_chars")
        if (
            any(not isinstance(item, int) or isinstance(item, bool) for item in (minimum, maximum, item_maximum))
            or not 0 <= minimum <= maximum <= MAX_PROFILE_ITEMS
            or not 1 <= item_maximum <= 2000
            or (spec["required"] and minimum < 1)
        ):
            _profile_error("string_list field bounds are invalid", {"field": name})
    elif kind in {"date", "timestamp", "context_id"}:
        expected = {"type", "required"}
    elif kind == "enum":
        expected = {"type", "required", "values"}
        values = _profile_string_list(spec.get("values"), f"fields.{name}.values")
        if not values:
            _profile_error("enum field requires at least one value", {"field": name})
    elif kind == "context_id_list":
        expected = {"type", "required", "min_items", "max_items"}
        minimum, maximum = spec.get("min_items"), spec.get("max_items")
        if (
            not isinstance(minimum, int) or isinstance(minimum, bool)
            or not isinstance(maximum, int) or isinstance(maximum, bool)
            or not 0 <= minimum <= maximum <= MAX_PROFILE_ITEMS
            or (spec["required"] and minimum < 1)
        ):
            _profile_error("context_id_list field bounds are invalid", {"field": name})
    else:
        expected = {"type", "required", "keys", "max_items"}
        keys = _profile_string_list(spec.get("keys"), f"fields.{name}.keys")
        maximum = spec.get("max_items")
        if not keys or not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= MAX_PROFILE_ITEMS:
            _profile_error("relation_map field bounds are invalid", {"field": name})
    if set(spec) != expected:
        _profile_error("structural profile field contains unknown or missing keys", {"field": name})


def _validate_structural_profile(profile: Any) -> None:
    if not isinstance(profile, dict) or set(profile) != {
        "schema", "fields", "sections", "index_projection", "lifecycle",
    } or profile.get("schema") != "context-structural-profile/v1":
        _profile_error("structural profile envelope is invalid")
    fields = profile.get("fields")
    if not isinstance(fields, dict) or not 1 <= len(fields) <= MAX_PROFILE_FIELDS:
        _profile_error("structural profile fields are invalid")
    for name, spec in fields.items():
        _validate_profile_field(name, spec)

    sections = profile.get("sections")
    if not isinstance(sections, dict) or set(sections) != {"ordered", "required", "primary"}:
        _profile_error("structural profile sections are invalid")
    ordered = _profile_string_list(sections.get("ordered"), "sections.ordered")
    required = _profile_string_list(sections.get("required"), "sections.required")
    primary = sections.get("primary")
    if (
        not 1 <= len(ordered) <= MAX_PROFILE_ITEMS
        or not required
        or any(name.startswith("#") for name in ordered)
        or any(name not in ordered for name in required)
        or [name for name in ordered if name in required] != required
        or primary not in required
    ):
        _profile_error("ordered, required, and primary sections are inconsistent")

    projection = _profile_string_list(profile.get("index_projection"), "index_projection", maximum=4)
    scalar_types = {"string", "date", "timestamp", "enum", "context_id"}
    if any(name not in fields or fields[name]["type"] not in scalar_types for name in projection):
        _profile_error("index projection must name declared scalar fields")

    lifecycle = profile.get("lifecycle")
    if not isinstance(lifecycle, dict) or set(lifecycle) != {"allowed_topologies", "reasons"}:
        _profile_error("structural lifecycle envelope is invalid")
    topologies = _profile_string_list(lifecycle.get("allowed_topologies"), "lifecycle.allowed_topologies", maximum=5)
    if not topologies or set(topologies) - PROFILE_TOPOLOGIES:
        _profile_error("structural lifecycle topology is invalid")
    reasons = lifecycle.get("reasons")
    if not isinstance(reasons, dict) or len(reasons) > MAX_PROFILE_ITEMS:
        _profile_error("structural lifecycle reasons are invalid")
    for reason, recipe in reasons.items():
        if not FIELD_KEY.fullmatch(reason) or not isinstance(recipe, dict) or set(recipe) != {
            "topology", "required_fields", "forbidden_fields", "successor", "references",
        }:
            _profile_error("structural lifecycle reason recipe is invalid", {"reason": reason})
        topology = recipe.get("topology")
        if topology not in {"retire_current", "supersede_current"} or topology not in topologies:
            _profile_error("lifecycle reason topology is invalid", {"reason": reason})
        required_fields = _profile_string_list(recipe.get("required_fields"), f"lifecycle.reasons.{reason}.required_fields")
        forbidden_fields = _profile_string_list(recipe.get("forbidden_fields"), f"lifecycle.reasons.{reason}.forbidden_fields")
        if (
            set(required_fields) & set(forbidden_fields)
            or any(name not in fields for name in [*required_fields, *forbidden_fields])
            or not {"retired_at", "retired_reason"}.issubset(required_fields)
        ):
            _profile_error("lifecycle required and forbidden fields are inconsistent", {"reason": reason})
        successor = recipe.get("successor")
        if successor not in {"required", "optional", "forbidden"}:
            _profile_error("lifecycle successor recipe is invalid", {"reason": reason})
        successor_spec = fields.get("superseded_by")
        if successor == "required" and (
            topology != "supersede_current"
            or not isinstance(successor_spec, dict)
            or successor_spec.get("type") != "context_id"
            or "superseded_by" not in required_fields
        ):
            _profile_error("required successor recipe lacks a required context_id field", {"reason": reason})
        if successor == "forbidden" and "superseded_by" not in forbidden_fields:
            _profile_error("forbidden successor must name superseded_by as forbidden", {"reason": reason})
        references = recipe.get("references")
        if not isinstance(references, list) or len(references) > 8:
            _profile_error("lifecycle references are invalid", {"reason": reason})
        seen_references: set[tuple[str, str, str, str]] = set()
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {"location", "field", "target", "match"}:
                _profile_error("lifecycle reference recipe contains unknown fields", {"reason": reason})
            key = (reference.get("location"), reference.get("field"), reference.get("target"), reference.get("match"))
            if key in seen_references:
                _profile_error("lifecycle reference recipe is duplicated", {"reason": reason})
            seen_references.add(key)
            location, field, target, match = key
            field_spec = fields.get(field)
            if (
                location not in {"predecessor", "successor"}
                or target not in {"predecessor", "successor"}
                or match not in {"equals", "contains"}
                or not isinstance(field_spec, dict)
                or (match == "equals" and field_spec.get("type") != "context_id")
                or (match == "contains" and field_spec.get("type") != "context_id_list")
                or (topology != "supersede_current" and (location == "successor" or target == "successor"))
            ):
                _profile_error("lifecycle reference recipe is not structurally compatible", {"reason": reason})
        if topology == "supersede_current":
            endpoint_pairs = {
                (reference["location"], reference["target"])
                for reference in references
            }
            if successor != "required" or not {
                ("predecessor", "successor"),
                ("successor", "predecessor"),
            }.issubset(endpoint_pairs):
                _profile_error(
                    "supersede_current requires successor and reciprocal reference recipes",
                    {"reason": reason},
                )
    if reasons:
        retired_at = fields.get("retired_at")
        retired_reason = fields.get("retired_reason")
        if (
            not isinstance(retired_at, dict) or retired_at.get("type") != "timestamp"
            or not isinstance(retired_reason, dict) or retired_reason.get("type") != "enum"
            or set(retired_reason.get("values", [])) != set(reasons)
        ):
            _profile_error("lifecycle reasons require matching retired_at and retired_reason field declarations")


def validate_owner_descriptor(descriptor: Any) -> tuple[str, str, str, str]:
    if not isinstance(descriptor, dict):
        _profile_error("owner descriptor must be an object")
    schema = descriptor.get("schema")
    if schema == "context-owner-descriptor/v1":
        if set(descriptor) != {"schema", "owner", "kind", "artifact_schema", "authority"}:
            _profile_error("area owner descriptor fields differ from context-owner-descriptor/v1")
    elif schema == "context-owner-descriptor/v2":
        if set(descriptor) != {"schema", "owner", "kind", "artifact_schema", "authority", "structural_profile"}:
            _profile_error("area owner descriptor fields differ from context-owner-descriptor/v2")
        _bounded_json_shape(descriptor)
        try:
            canonical_descriptor = canonical_json(descriptor)
            normalized_descriptor = _canonical_value(descriptor)
        except ContextError as error:
            raise ContextError("owner_descriptor_invalid", "owner descriptor is not canonical JSON", error.details, EXIT_CONFLICT) from error
        if len(canonical_descriptor.encode("utf-8")) > MAX_OWNER_DESCRIPTOR_BYTES:
            _profile_error("owner descriptor exceeds 8 KiB")
        if normalized_descriptor != descriptor:
            _profile_error("owner descriptor strings are not canonical NFC")
        _validate_structural_profile(descriptor["structural_profile"])
    else:
        _profile_error("owner descriptor schema is unsupported")
    owner = descriptor.get("owner")
    area = descriptor.get("kind")
    artifact_schema = descriptor.get("artifact_schema")
    authority = descriptor.get("authority")
    if schema == "context-owner-descriptor/v1":
        if not all(isinstance(value, str) and value for value in (owner, area, artifact_schema, authority)) or not AREA_NAME.fullmatch(area):
            _profile_error("area owner descriptor identity is invalid")
    elif (
        not isinstance(owner, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", owner)
        or not isinstance(area, str) or not AREA_NAME.fullmatch(area)
        or not isinstance(artifact_schema, str) or not re.fullmatch(r"context-[a-z][a-z0-9-]*/v[1-9][0-9]*", artifact_schema)
        or authority not in {"staging", "evidence", "provisional", "authoritative"}
    ):
        _profile_error("v2 owner descriptor identity or authority is invalid")
    return owner, area, artifact_schema, authority


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_digest(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def file_bytes(content: str) -> bytes:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    return normalized.encode("utf-8")


def new_context_id() -> str:
    return "ctx_" + uuid.uuid4().hex


def new_plan_id() -> str:
    return "plan_" + uuid.uuid4().hex


def is_context_id(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"ctx_[0-9a-f]{32}", value):
        return False
    parsed = uuid.UUID(hex=value[4:])
    return parsed.version == 4 and parsed.variant == uuid.RFC_4122


def _require_context_id(value: Any, field: str = "id") -> str:
    if not is_context_id(value):
        raise ContextError("id_invalid", f"{field} must be ctx_ plus lowercase UUIDv4 hex", {"field": field})
    return value


def natural_filename(title: str) -> str:
    title = nfc(title.strip())
    output: list[str] = []
    separator = False
    for char in title:
        if char.isalnum() or char in "-_.":
            output.append(char)
            separator = False
        elif not separator:
            output.append("-")
            separator = True
    stem = "".join(output).strip("-._")
    if not stem:
        raise ContextError("filename_required", "title cannot produce a safe filename")
    return validate_filename(stem + ".md")


def validate_filename(value: str) -> str:
    if not isinstance(value, str):
        raise ContextError("filename_invalid", "filename must be a string")
    value = nfc(value)
    if value.endswith(".md"):
        basename = value
    elif "." not in pathlib.PurePosixPath(value).name:
        basename = value + ".md"
    else:
        raise ContextError("filename_invalid", "filename extension must be .md", {"filename": value})
    stem = basename[:-3]
    folded = normalized_key(basename)
    folded_stem = normalized_key(stem)
    if not stem or stem in {".", ".."} or basename.endswith((" ", ".")):
        raise ContextError("filename_invalid", "filename has an invalid stem", {"filename": value})
    if any(char in FILENAME_FORBIDDEN or ord(char) < 32 or ord(char) == 127 for char in basename):
        raise ContextError("filename_invalid", "filename contains a forbidden character", {"filename": value})
    if "<!--" in folded or "-->" in folded or folded.endswith(".index.md"):
        raise ContextError("reserved_path", "artifact filename is reserved", {"filename": value}, EXIT_CONFLICT)
    if WINDOWS_RESERVED.fullmatch(folded_stem):
        raise ContextError("filename_invalid", "filename is reserved by supported filesystems", {"filename": value})
    if len(basename) > 120 or len(basename.encode("utf-8")) > 240:
        raise ContextError("filename_required", "filename exceeds the v1 limit", {"filename": value})
    return basename


def _ensure_contained(repo: pathlib.Path, relative: str) -> pathlib.Path:
    pure = pathlib.PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ContextError("path_escape", "path must be a canonical repository-relative POSIX path", {"path": relative}, EXIT_CONFLICT)
    candidate = repo.joinpath(*pure.parts)
    repo_real = repo.resolve()
    current = repo_real
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ContextError("symlink_path", "symlink path segments are not writable", {"path": relative}, EXIT_CONFLICT)
    try:
        candidate.resolve(strict=False).relative_to(repo_real)
    except ValueError as error:
        raise ContextError("path_escape", "path escapes repository root", {"path": relative}, EXIT_CONFLICT) from error
    return candidate


def resolve_artifact_path(repo: pathlib.Path, area: str, filename: str, *, existing_path: str | None = None) -> pathlib.Path:
    if not AREA_NAME.fullmatch(area):
        raise ContextError("area_invalid", "invalid area name")
    basename = validate_filename(filename)
    relative = f"context/{area}/{basename}"
    candidate = _ensure_contained(repo, relative)
    area_root = _ensure_contained(repo, f"context/{area}")
    if area_root.exists():
        key = normalized_key(basename)
        with os.scandir(area_root) as entries:
            for entry in entries:
                if entry.name == "retired" or (existing_path and f"context/{area}/{entry.name}" == existing_path):
                    continue
                if normalized_key(entry.name) == key:
                    raise ContextError("path_exists", "a collision-equivalent path already exists", {"path": relative}, EXIT_CONFLICT)
    return candidate


def _newline_normalized(text: str) -> str:
    if text.startswith("\ufeff"):
        raise ContextError("frontmatter_unsupported", "UTF-8 BOM is not supported")
    has_crlf = "\r\n" in text
    without_crlf = text.replace("\r\n", "")
    if "\r" in without_crlf or (has_crlf and "\n" in without_crlf):
        raise ContextError("frontmatter_unsupported", "mixed or bare-CR newlines are not supported")
    return text.replace("\r\n", "\n")


def _valid_yaml_value(value: Any, *, object_value: bool = False) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, list):
        return all(isinstance(item, str) for item in value)
    if isinstance(value, dict) and not object_value:
        return all(isinstance(key, str) and _valid_yaml_value(item, object_value=True) for key, item in value.items())
    return False


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], list[str], int]:
    text = _newline_normalized(text)
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise ContextError("frontmatter_unsupported", "the first line must be exactly ---")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise ContextError("frontmatter_unsupported", "closing frontmatter delimiter is missing") from error
    frontmatter: dict[str, Any] = {}
    for line in lines[1:closing]:
        if not line or line.lstrip().startswith("#"):
            raise ContextError("frontmatter_unsupported", "blank lines and comments are forbidden in frontmatter")
        if ": " not in line:
            raise ContextError("frontmatter_unsupported", "frontmatter fields must be KEY: JSON_VALUE")
        key, raw = line.split(": ", 1)
        if not FIELD_KEY.fullmatch(key) or key in frontmatter:
            raise ContextError("frontmatter_unsupported", "invalid or duplicate frontmatter key", {"key": key})
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ContextError("frontmatter_unsupported", "frontmatter values must be compact JSON", {"key": key}) from error
        if compact_json(value) != raw or not _valid_yaml_value(value):
            raise ContextError("frontmatter_unsupported", "frontmatter value is outside the JSON-compatible subset", {"key": key})
        frontmatter[key] = value
    if closing + 1 >= len(lines) or lines[closing + 1] != "":
        raise ContextError("frontmatter_unsupported", "frontmatter must be followed by one blank line")
    return frontmatter, lines, closing


def _validate_timestamp(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise ContextError("schema_invalid", f"{field} must be a timestamp")
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as error:
        raise ContextError("schema_invalid", f"{field} must be RFC3339-compatible") from error
    if parsed.tzinfo is None or parsed.isoformat(timespec="seconds") != value:
        raise ContextError("schema_invalid", f"{field} must include an offset and seconds precision")


def _timestamp(value: str | None = None) -> str:
    resolved = value or datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(resolved, "timestamp")
    return resolved


def _substantive(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.strip() not in PLACEHOLDERS


def _string_list(value: Any, field: str, *, required: bool = False, maximum: int = 12, item_maximum: int = 500) -> list[str]:
    if not isinstance(value, list) or any(not _substantive(item) or "\n" in item or len(item) > item_maximum for item in value):
        raise ContextError(
            "schema_invalid",
            f"{field} must be a list of short items: one item per line (an optional `- ` bullet), each non-empty "
            f"and at most {item_maximum} characters",
        )
    if (required and not value) or len(value) > maximum:
        raise ContextError("schema_invalid", f"{field} has an invalid item count")
    return [nfc(item.strip()) for item in value]


def _validate_date(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise ContextError("schema_invalid", f"{field} must be a date")
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError as error:
        raise ContextError("schema_invalid", f"{field} must be an ISO date") from error
    if parsed.isoformat() != value:
        raise ContextError("schema_invalid", f"{field} must be a canonical ISO date")


def _descriptor_profile(descriptor: dict[str, Any] | None) -> dict[str, Any] | None:
    if descriptor is None:
        return None
    validate_owner_descriptor(descriptor)
    return descriptor.get("structural_profile") if descriptor.get("schema") == "context-owner-descriptor/v2" else None


def _validate_profile_value(name: str, value: Any, spec: dict[str, Any]) -> None:
    kind = spec["type"]
    if kind == "string":
        if (
            not isinstance(value, str)
            or "\n" in value
            or not spec["min_chars"] <= len(value) <= spec["max_chars"]
            or (spec["min_chars"] > 0 and not value.strip())
        ):
            raise ContextError("schema_invalid", "profiled string field violates its declared bounds", {"field": name})
    elif kind == "string_list":
        if (
            not isinstance(value, list)
            or not spec["min_items"] <= len(value) <= spec["max_items"]
            or len(value) != len(set(value))
            or any(not isinstance(item, str) or not item.strip() or "\n" in item or len(item) > spec["max_item_chars"] for item in value)
        ):
            raise ContextError("schema_invalid", "profiled string_list field violates its declared bounds", {"field": name})
    elif kind == "date":
        _validate_date(value, name)
    elif kind == "timestamp":
        _validate_timestamp(value, name)
    elif kind == "enum":
        if value not in spec["values"]:
            raise ContextError("schema_invalid", "profiled enum field has an undeclared value", {"field": name})
    elif kind == "context_id":
        _require_context_id(value, name)
    elif kind == "context_id_list":
        if (
            not isinstance(value, list)
            or not spec["min_items"] <= len(value) <= spec["max_items"]
            or len(value) != len(set(value))
        ):
            raise ContextError("schema_invalid", "profiled context_id_list violates its declared bounds", {"field": name})
        for identifier in value:
            _require_context_id(identifier, name)
    elif kind == "relation_map":
        if not isinstance(value, dict) or set(value) - set(spec["keys"]):
            raise ContextError("schema_invalid", "profiled relation_map has undeclared keys", {"field": name})
        for relation, identifiers in value.items():
            if (
                not isinstance(identifiers, list)
                or len(identifiers) > spec["max_items"]
                or len(identifiers) != len(set(identifiers))
            ):
                raise ContextError("schema_invalid", "profiled relation_map values are invalid", {"field": name, "relation": relation})
            for identifier in identifiers:
                _require_context_id(identifier, name)


def _validate_common_document(
    frontmatter: dict[str, Any],
    descriptor: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    removed = sorted(REMOVED_FINGERPRINT_FIELDS & set(frontmatter))
    warnings = (
        ({"code": "schema_removed_field", "fields": removed},)
        if removed
        else ()
    )
    required = ("schema", "id", "title", "summary", "created_at", "captured_from")
    missing = [key for key in required if key not in frontmatter]
    if missing:
        raise ContextError("schema_invalid", "required frontmatter field is missing", {"missing": missing})
    schema = frontmatter["schema"]
    profile = _descriptor_profile(descriptor)
    if profile is None and schema not in SECTION_SPECS:
        raise ContextError("schema_invalid", "unsupported artifact schema", {"schema": schema})
    if profile is not None and schema != descriptor["artifact_schema"]:
        raise ContextError("schema_invalid", "artifact schema differs from its structural profile", {"schema": schema})
    _require_context_id(frontmatter["id"])
    for field, maximum in (("title", 120), ("summary", 280)):
        value = frontmatter[field]
        if not isinstance(value, str) or not value.strip() or "\n" in value or len(value) > maximum:
            raise ContextError("schema_invalid", f"{field} is invalid")
    if frontmatter["captured_from"] not in {"conversation", "workspace", "manual", "import"}:
        raise ContextError("schema_invalid", "captured_from is invalid")
    _validate_timestamp(frontmatter["created_at"], "created_at")
    for field in ("updated_at", "verified_at", "retired_at"):
        if field in frontmatter:
            _validate_timestamp(frontmatter[field], field)
    created = datetime.datetime.fromisoformat(frontmatter["created_at"])
    for field in ("updated_at", "verified_at", "retired_at"):
        if field in frontmatter and datetime.datetime.fromisoformat(frontmatter[field]) < created:
            raise ContextError("clock_invalid", f"{field} cannot precede created_at", exit_code=EXIT_CONFLICT)
    for field, maximum, item_maximum in (("tags", 12, 40), ("search_terms", 12, 40), ("source_refs", 12, 500)):
        if field in frontmatter:
            _string_list(frontmatter[field], field, maximum=maximum, item_maximum=item_maximum)
    if profile is not None:
        fields = profile["fields"]
        unknown = set(frontmatter) - PROFILE_COMMON_FIELDS - set(fields) - REMOVED_FINGERPRINT_FIELDS
        if unknown:
            raise ContextError("schema_invalid", "artifact frontmatter contains undeclared profiled fields", {"fields": sorted(unknown)})
        for name, spec in fields.items():
            if spec["required"] and name not in frontmatter:
                raise ContextError("schema_invalid", "required profiled field is missing", {"field": name})
            if name in frontmatter:
                _validate_profile_value(name, frontmatter[name], spec)
        return warnings
    if schema == "context-snapshot/v1":
        if "anchors" in frontmatter:
            anchors = _string_list(frontmatter["anchors"], "anchors", maximum=12, item_maximum=36)
            for identifier in anchors:
                _require_context_id(identifier, "anchors")
        forbidden = {"verified_at", "retired_at", "retired_reason", "retirement_note", "supersedes", "superseded_by"}
        if forbidden & set(frontmatter):
            raise ContextError("lifecycle_invalid", "snapshot cannot carry history or verification fields")
    if schema == "context-observation/v1":
        if frontmatter.get("kind_hint") not in {None, "decision"}:
            raise ContextError("schema_invalid", "observation kind_hint is invalid")
        if frontmatter.get("retired_reason") not in {None, "invalidated", "superseded"}:
            raise ContextError("lifecycle_invalid", "observation retired_reason is invalid")
        if frontmatter.get("retired_reason") == "invalidated" and not _substantive(frontmatter.get("retirement_note")):
            raise ContextError("lifecycle_invalid", "invalidated observation requires retirement_note")
        if frontmatter.get("retired_reason") == "invalidated" and "superseded_by" in frontmatter:
            raise ContextError("lifecycle_invalid", "invalidated observation cannot name a successor")
        if frontmatter.get("retired_reason") == "superseded":
            _require_context_id(frontmatter.get("superseded_by"), "superseded_by")
        if frontmatter.get("retired_reason") is None and any(key in frontmatter for key in ("superseded_by", "retirement_note")):
            raise ContextError("lifecycle_invalid", "observation lifecycle fields require retired_reason")
        if "supersedes" in frontmatter:
            for identifier in _string_list(frontmatter["supersedes"], "supersedes", maximum=12, item_maximum=36):
                _require_context_id(identifier, "supersedes")
    if schema == "context-archive/v1":
        _string_list(frontmatter.get("source_refs"), "source_refs", required=True, maximum=12, item_maximum=500)
        forbidden = {
            "updated_at", "verified_at", "retired_at", "retired_reason", "retirement_note",
            "supersedes", "superseded_by", "relations",
        }
        if forbidden & set(frontmatter):
            raise ContextError("lifecycle_invalid", "archive is immutable and cannot carry mutation or lifecycle fields")
    if schema == "context-decision/v1":
        for field in ("scope", "decision_key"):
            if not _substantive(frontmatter.get(field)) or "\n" in frontmatter[field]:
                raise ContextError("schema_invalid", f"decision {field} is invalid")
        if (
            frontmatter["scope"] != _canonical_decision_scope(frontmatter["scope"])
            or frontmatter["decision_key"] != _canonical_decision_key(frontmatter["decision_key"])
        ):
            raise ContextError("schema_invalid", "decision slot fields must already be canonical")
        if "supersedes" in frontmatter:
            for identifier in _string_list(frontmatter["supersedes"], "supersedes", maximum=12, item_maximum=36):
                _require_context_id(identifier, "supersedes")
    return warnings


def parse_document(text: str, descriptor: dict[str, Any] | None = None) -> Document:
    frontmatter, lines, closing = _parse_frontmatter(text)
    warnings = _validate_common_document(frontmatter, descriptor)
    schema = frontmatter["schema"]
    profile = _descriptor_profile(descriptor)
    raw_allowed, raw_required = (
        (tuple(profile["sections"]["ordered"]), tuple(profile["sections"]["required"]))
        if profile is not None
        else SECTION_SPECS[schema]
    )
    allowed = tuple(_canonical_section_name(schema, name) for name in raw_allowed)
    required = tuple(_canonical_section_name(schema, name) for name in raw_required)
    sections: dict[str, str] = {}
    current: str | None = None
    current_canonical: str | None = None
    seen_canonical: set[str] = set()
    section_style: str | None = None
    buffer: list[str] = []
    in_fence: str | None = None
    for line in lines[closing + 2 :]:
        fence_match = re.match(r"^\s*(```+|~~~+)", line)
        if fence_match:
            marker = fence_match.group(1)[0]
            in_fence = None if in_fence == marker else (marker if in_fence is None else in_fence)
        heading = re.fullmatch(r"## (.+)", line) if in_fence is None else None
        if heading:
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            name = heading.group(1)
            canonical = _canonical_section_name(schema, name)
            style = "legacy" if name in LEGACY_SECTION_ALIASES.get(schema, {}) else "canonical"
            if section_style is not None and style != section_style:
                raise ContextError("section_schema_error", "canonical and legacy section headings cannot be mixed", {"section": name})
            if (
                canonical not in allowed
                or canonical in seen_canonical
                or (current_canonical and allowed.index(canonical) <= allowed.index(current_canonical))
            ):
                raise ContextError("section_schema_error", "unknown, duplicate, or out-of-order H2 section", {"section": name})
            current = name
            current_canonical = canonical
            seen_canonical.add(canonical)
            section_style = style
            buffer = []
        else:
            if current is None and line.strip():
                raise ContextError("section_schema_error", "content before the first section is forbidden")
            if current is not None:
                buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    for name in required:
        content = (_section_value(sections, schema, name) or "").strip()
        if not content or content in PLACEHOLDERS:
            raise ContextError("section_schema_error", "required section is missing or placeholder", {"section": name})
    if schema == "context-archive/v1":
        content = _section_value(sections, schema, "Content") or ""
        if len(content) > MAX_ARCHIVE_CONTENT_CODEPOINTS:
            raise ContextError(
                "section_schema_error",
                "archive Content exceeds the 65,000-codepoint limit",
                {"section": "Content", **_codepoint_size_details(len(content), MAX_ARCHIVE_CONTENT_CODEPOINTS)},
            )
    return Document(frontmatter=frontmatter, sections=sections, warnings=warnings)


def render_document(
    frontmatter: dict[str, Any],
    sections: dict[str, str],
    descriptor: dict[str, Any] | None = None,
) -> str:
    canonical_frontmatter = {
        key: value
        for key, value in frontmatter.items()
        if key not in REMOVED_FINGERPRINT_FIELDS
    }
    _validate_common_document(canonical_frontmatter, descriptor)
    schema = canonical_frontmatter["schema"]
    profile = _descriptor_profile(descriptor)
    raw_allowed, raw_required = (
        (tuple(profile["sections"]["ordered"]), tuple(profile["sections"]["required"]))
        if profile is not None
        else SECTION_SPECS[schema]
    )
    allowed = tuple(_canonical_section_name(schema, name) for name in raw_allowed)
    required = tuple(_canonical_section_name(schema, name) for name in raw_required)
    canonical_to_actual: dict[str, str] = {}
    styles: set[str] = set()
    for name in sections:
        canonical = _canonical_section_name(schema, name)
        if canonical in canonical_to_actual:
            raise ContextError("section_schema_error", "canonical and legacy aliases cannot both be rendered", {"section": canonical})
        canonical_to_actual[canonical] = name
        styles.add("legacy" if name in LEGACY_SECTION_ALIASES.get(schema, {}) else "canonical")
    unknown = set(canonical_to_actual) - set(allowed)
    if unknown:
        raise ContextError("section_schema_error", "unknown sections cannot be rendered", {"sections": sorted(unknown)})
    if len(styles) > 1:
        raise ContextError("section_schema_error", "canonical and legacy section headings cannot be mixed")
    for name in required:
        content = (_section_value(sections, schema, name) or "").strip()
        if not content or content in PLACEHOLDERS:
            raise ContextError("section_schema_error", "required section is missing or placeholder", {"section": name})
    if schema == "context-archive/v1":
        content = _section_value(sections, schema, "Content") or ""
        if len(content) > MAX_ARCHIVE_CONTENT_CODEPOINTS:
            raise ContextError(
                "section_schema_error",
                "archive Content exceeds the 65,000-codepoint limit",
                {"section": "Content", **_codepoint_size_details(len(content), MAX_ARCHIVE_CONTENT_CODEPOINTS)},
            )
    known = COMMON_KEY_ORDER + (tuple(profile["fields"]) if profile is not None else ADDITIVE_KEY_ORDER.get(schema, ()))
    ordered = [key for key in known if key in canonical_frontmatter]
    ordered.extend(sorted(set(canonical_frontmatter) - set(ordered)))
    lines = ["---"]
    for key in ordered:
        value = canonical_frontmatter[key]
        if not _valid_yaml_value(value):
            raise ContextError("frontmatter_unsupported", "frontmatter value is outside the supported subset", {"key": key})
        lines.append(f"{key}: {compact_json(value)}")
    lines.extend(["---", ""])
    for name in allowed:
        actual = canonical_to_actual.get(name)
        if actual is not None:
            lines.extend([f"## {actual}", "", sections[actual].strip(), ""])
    return "\n".join(lines).rstrip("\n") + "\n"


def _parse_index_frontmatter(text: str, expected_schema: str) -> dict[str, Any]:
    frontmatter, _, _ = _parse_frontmatter(text)
    if frontmatter.get("schema") != expected_schema or frontmatter.get("index") is not True:
        raise ContextError("index_stale", "index frontmatter schema is invalid", {"expected_schema": expected_schema}, EXIT_INTEGRITY)
    return frontmatter


def _extract_block(text: str, name: str) -> list[str]:
    begin = f"<!-- BEGIN CONTEXT GENERATED:{name} -->"
    end = f"<!-- END CONTEXT GENERATED:{name} -->"
    if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) > text.index(end):
        raise ContextError("index_marker_invalid", "generated index marker is invalid", {"block": name}, EXIT_INTEGRITY)
    inside = text.split(begin, 1)[1].split(end, 1)[0]
    return [line for line in inside.strip("\n").split("\n") if line]


def _replace_block(text: str, name: str, rows: Sequence[str]) -> str:
    begin = f"<!-- BEGIN CONTEXT GENERATED:{name} -->"
    end = f"<!-- END CONTEXT GENERATED:{name} -->"
    _extract_block(text, name)
    before, remainder = text.split(begin, 1)
    _, after = remainder.split(end, 1)
    body = "\n".join(rows)
    middle = f"{begin}\n{body + chr(10) if body else ''}{end}"
    return (before + middle + after).rstrip("\n") + "\n"


def parse_root_profiles(text: str) -> list[dict[str, Any]]:
    begin = "<!-- BEGIN CONTEXT GENERATED:owner-profiles -->"
    end = "<!-- END CONTEXT GENERATED:owner-profiles -->"
    if begin not in text and end not in text:
        return []
    rows: list[dict[str, Any]] = []
    for line in _extract_block(text, "owner-profiles"):
        match = OWNER_PROFILE_ROW.fullmatch(line)
        if not match:
            raise ContextError("index_noncanonical", "root owner profile row is malformed", exit_code=EXIT_INTEGRITY)
        value = _strict_json_loads(match.group(1), code="index_noncanonical")
        if (
            not isinstance(value, dict)
            or list(value) != ["area", "descriptor_schema", "descriptor_digest"]
            or compact_json(value) != match.group(1)
            or not isinstance(value.get("area"), str)
            or not AREA_NAME.fullmatch(value["area"])
            or value.get("descriptor_schema") != "context-owner-descriptor/v2"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value.get("descriptor_digest")))
        ):
            raise ContextError("index_noncanonical", "root owner profile row is not canonical", exit_code=EXIT_INTEGRITY)
        rows.append(value)
    if rows != sorted(rows, key=lambda row: row["area"]) or len({row["area"] for row in rows}) != len(rows):
        raise ContextError("index_noncanonical", "root owner profile rows are duplicated or unsorted", exit_code=EXIT_INTEGRITY)
    return rows


def render_root_profiles(text: str, profiles: Sequence[dict[str, Any]]) -> str:
    rows = [
        f"<!-- context-owner-profile {compact_json(profile)} -->"
        for profile in sorted(profiles, key=lambda item: item["area"])
    ]
    begin = "<!-- BEGIN CONTEXT GENERATED:owner-profiles -->"
    end = "<!-- END CONTEXT GENERATED:owner-profiles -->"
    if begin in text or end in text:
        if not rows:
            exact_start = text.find("\n\n## Owner Profiles\n" + begin)
            if exact_start >= 0 and text.rstrip("\n").endswith(end):
                return text[:exact_start].rstrip("\n") + "\n"
        return _replace_block(text, "owner-profiles", rows)
    if not rows:
        return text
    block = "\n".join(["## Owner Profiles", begin, *rows, end])
    return text.rstrip("\n") + "\n\n" + block + "\n"


def parse_area_profile(text: str) -> dict[str, Any] | None:
    begin = "<!-- BEGIN CONTEXT GENERATED:owner-profile -->"
    end = "<!-- END CONTEXT GENERATED:owner-profile -->"
    if begin not in text and end not in text:
        return None
    lines = _extract_block(text, "owner-profile")
    if len(lines) != 1:
        raise ContextError("owner_profile_invalid", "area owner profile block must contain one descriptor", exit_code=EXIT_INTEGRITY)
    descriptor = _strict_json_loads(lines[0], code="owner_profile_invalid")
    try:
        validate_owner_descriptor(descriptor)
    except ContextError as error:
        raise ContextError("owner_profile_invalid", error.message, error.details, EXIT_INTEGRITY) from error
    if descriptor.get("schema") != "context-owner-descriptor/v2" or canonical_json(descriptor) != lines[0]:
        raise ContextError("owner_profile_invalid", "area owner profile descriptor is not canonical v2 JSON", exit_code=EXIT_INTEGRITY)
    return descriptor


def render_area_profile(text: str, descriptor: dict[str, Any]) -> str:
    validate_owner_descriptor(descriptor)
    if descriptor.get("schema") != "context-owner-descriptor/v2" or parse_area_profile(text) is not None:
        raise ContextError("owner_profile_invalid", "area owner profile can only be added once for a v2 descriptor", exit_code=EXIT_CONFLICT)
    _, lines, closing = _parse_frontmatter(text)
    insert_at = closing + 2
    block = [
        "<!-- BEGIN CONTEXT GENERATED:owner-profile -->",
        canonical_json(descriptor),
        "<!-- END CONTEXT GENERATED:owner-profile -->",
        "",
    ]
    return "\n".join([*lines[:insert_at], *block, *lines[insert_at:]]).rstrip("\n") + "\n"


def parse_root_index(text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    frontmatter = _parse_index_frontmatter(text, "context-root-index/v1")
    if frontmatter.get("owner") != "context-core":
        raise ContextError("index_stale", "root index owner must be context-core", exit_code=EXIT_INTEGRITY)
    areas: list[dict[str, Any]] = []
    for line in _extract_block(text, "areas"):
        match = ROOT_ROW.fullmatch(line)
        if not match:
            raise ContextError("index_noncanonical", "root area row is malformed", exit_code=EXIT_INTEGRITY)
        try:
            row = json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise ContextError("index_noncanonical", "root area row JSON is malformed", exit_code=EXIT_INTEGRITY) from error
        expected = ["area", "path", "owner", "claims", "artifact_schema", "authority"]
        if list(row) != expected or compact_json(row) != match.group(1) or row.get("claims") != [row.get("area")]:
            raise ContextError("index_noncanonical", "root area row fields are not canonical", exit_code=EXIT_INTEGRITY)
        area = row.get("area")
        path = row.get("path")
        if not isinstance(area, str) or not AREA_NAME.fullmatch(area) or not isinstance(path, str):
            raise ContextError("path_escape", "root area path is not canonical", {"path": path}, EXIT_INTEGRITY)
        pure = pathlib.PurePosixPath(path)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ContextError("path_escape", "root area path escapes its canonical location", {"path": path}, EXIT_INTEGRITY)
        if path == ROOT_INDEX or area == "context":
            raise ContextError("index_self_entry", "root index cannot catalog itself", {"path": path}, EXIT_INTEGRITY)
        if path != f"context/{area}/{area}.index.md":
            raise ContextError("reserved_index_path", "area index path is not canonical", {"path": path}, EXIT_INTEGRITY)
        if any(not isinstance(row.get(key), str) or not row[key] for key in ("owner", "artifact_schema", "authority")):
            raise ContextError("index_noncanonical", "root area descriptor is incomplete", exit_code=EXIT_INTEGRITY)
        areas.append(row)
    if areas != sorted(areas, key=lambda row: row["area"]):
        raise ContextError("index_stale", "root area rows are not sorted", exit_code=EXIT_INTEGRITY)
    profiles = parse_root_profiles(text)
    if any(profile["area"] not in {area["area"] for area in areas} for profile in profiles):
        raise ContextError("index_noncanonical", "root owner profile lacks a matching area row", exit_code=EXIT_INTEGRITY)
    return frontmatter, areas


def _parse_area_index_metadata(text: str) -> dict[str, Any]:
    frontmatter = _parse_index_frontmatter(text, "context-area-index/v1")
    required = ("area", "owner", "artifact_schema", "authority", "summary")
    if any(not isinstance(frontmatter.get(key), str) or not frontmatter[key] for key in required):
        raise ContextError("index_stale", "area index metadata is incomplete", exit_code=EXIT_INTEGRITY)
    if not AREA_NAME.fullmatch(frontmatter["area"]):
        raise ContextError("index_stale", "area index name is invalid", exit_code=EXIT_INTEGRITY)
    projection_fields = frontmatter.get("projection_fields", [])
    if (
        not isinstance(projection_fields, list)
        or len(projection_fields) > 4
        or any(not isinstance(field, str) or not FIELD_KEY.fullmatch(field) for field in projection_fields)
        or len(projection_fields) != len(set(projection_fields))
        or set(projection_fields) & {"id", "path", "title", "summary", "state", "created_at", "updated_at", "terms", "retired_at", "retired_reason", "superseded_by"}
    ):
        raise ContextError("index_noncanonical", "projection_fields are invalid", exit_code=EXIT_INTEGRITY)
    return frontmatter


def parse_area_index(text: str) -> AreaIndex:
    frontmatter = _parse_area_index_metadata(text)
    descriptor = parse_area_profile(text)
    if descriptor is not None:
        owner, area, artifact_schema, authority = validate_owner_descriptor(descriptor)
        if (
            (frontmatter["area"], frontmatter["owner"], frontmatter["artifact_schema"], frontmatter["authority"])
            != (area, owner, artifact_schema, authority)
            or frontmatter.get("projection_fields", []) != descriptor["structural_profile"]["index_projection"]
        ):
            raise ContextError("owner_profile_invalid", "area owner profile differs from index metadata", exit_code=EXIT_INTEGRITY)
    projection_fields = frontmatter.get("projection_fields", [])
    history_required = frontmatter["area"] != "snapshot"
    if not history_required and ("CONTEXT GENERATED:history" in text):
        raise ContextError("index_noncanonical", "snapshot index cannot contain history", exit_code=EXIT_INTEGRITY)
    blocks = (("current", "current"),) + (("history", "history"),) if history_required else (("current", "current"),)
    parsed: dict[str, list[dict[str, Any]]] = {"current": [], "history": []}
    seen: set[str] = set()
    for block, expected_state in blocks:
        for line in _extract_block(text, block):
            match = ENTRY_ROW.fullmatch(line)
            if not match:
                raise ContextError("index_noncanonical", "area entry row is malformed", {"block": block}, EXIT_INTEGRITY)
            try:
                row = json.loads(match.group(1))
            except json.JSONDecodeError as error:
                raise ContextError("index_noncanonical", "area entry JSON is malformed", exit_code=EXIT_INTEGRITY) from error
            if row.get("state") != expected_state:
                raise ContextError("index_wrong_state", "area entry is in the wrong generated block", {"id": row.get("id")}, EXIT_INTEGRITY)
            if not is_context_id(row.get("id")):
                raise ContextError("index_noncanonical", "area entry id is invalid", exit_code=EXIT_INTEGRITY)
            if row["id"] in seen:
                raise ContextError("index_duplicate_entry", "area index contains a duplicate id", {"id": row["id"]}, EXIT_INTEGRITY)
            seen.add(row["id"])
            base = ["id", "path", "title", "summary", "state", "created_at"]
            if "updated_at" in row:
                base.append("updated_at")
            base.append("terms")
            if expected_state == "history":
                base.extend(("retired_at", "retired_reason"))
                if "superseded_by" in row:
                    base.append("superseded_by")
            base.extend(field for field in projection_fields if field in row)
            if list(row) != base or compact_json(row) != match.group(1):
                raise ContextError("index_noncanonical", "area entry fields or JSON bytes are not canonical", {"id": row["id"]}, EXIT_INTEGRITY)
            if (
                any(not isinstance(row.get(field), str) or not row[field] for field in ("path", "title", "summary", "created_at"))
                or not isinstance(row.get("terms"), list)
                or any(not isinstance(term, str) for term in row["terms"])
            ):
                raise ContextError("index_noncanonical", "area entry fields are invalid", {"id": row["id"]}, EXIT_INTEGRITY)
            path = row["path"]
            pure = pathlib.PurePosixPath(path)
            if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                raise ContextError("path_escape", "area entry path is not canonical", {"path": path}, EXIT_INTEGRITY)
            if path in RESERVED_INDEX_PATHS or path.endswith(".index.md"):
                raise ContextError("index_self_entry", "reserved index cannot be an artifact row", {"path": path}, EXIT_INTEGRITY)
            prefix = f"context/{frontmatter['area']}/"
            expected_history = path.startswith(prefix + "retired/")
            if not path.startswith(prefix) or not path.endswith(".md"):
                raise ContextError("path_escape", "area entry path escapes its area", {"path": path}, EXIT_INTEGRITY)
            if expected_history != (expected_state == "history"):
                raise ContextError("index_wrong_state", "area entry path and state disagree", {"path": path}, EXIT_INTEGRITY)
            if _entry_row(row) != line:
                raise ContextError("index_noncanonical", "area entry visible row is not canonical", {"id": row["id"]}, EXIT_INTEGRITY)
            parsed[block].append(row)
        if parsed[block] != sorted(parsed[block], key=lambda row: (row["created_at"], row["id"])):
            raise ContextError("index_noncanonical", "area entry rows are not sorted", {"block": block}, EXIT_INTEGRITY)
    return AreaIndex(frontmatter=frontmatter, current=parsed["current"], history=parsed["history"], text=text)


def _markdown_escape(value: str) -> str:
    escaped = value
    for char in "\\`*_{}[]<>#|":
        escaped = escaped.replace(char, "\\" + char)
    return escaped.replace("\n", " ")


def _terms(frontmatter: dict[str, Any]) -> list[str]:
    values = list(frontmatter.get("tags", [])) + list(frontmatter.get("search_terms", []))
    selected: dict[str, str] = {}
    for value in values:
        value = nfc(value.strip())
        key = normalized_key(value)
        if value and (key not in selected or value < selected[key]):
            selected[key] = value
    return [selected[key] for key in sorted(selected)]


def _read_artifact_text(path: pathlib.Path, metrics: IOMetrics | None = None) -> str:
    if metrics:
        metrics.artifact_opens += 1
    raw = path.read_bytes()
    if metrics:
        metrics.artifact_read_bytes += len(raw)
    return raw.decode("utf-8")


def _entry_from_document(
    repo: pathlib.Path,
    path: pathlib.Path,
    metadata: dict[str, Any],
    state: str,
    metrics: IOMetrics | None = None,
    descriptor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = parse_document(_read_artifact_text(path, metrics), descriptor)
    fm = document.frontmatter
    expected_schema = metadata["artifact_schema"]
    if fm["schema"] != expected_schema:
        raise ContextError("schema_area_mismatch", "artifact schema does not match area", {"path": path.relative_to(repo).as_posix()}, EXIT_INTEGRITY)
    row: dict[str, Any] = {
        "id": fm["id"],
        "path": path.relative_to(repo).as_posix(),
        "title": fm["title"],
        "summary": fm["summary"],
        "state": state,
        "created_at": fm["created_at"],
    }
    if "updated_at" in fm:
        row["updated_at"] = fm["updated_at"]
    row["terms"] = _terms(fm)
    if state == "history":
        for key in ("retired_at", "retired_reason"):
            if key not in fm:
                raise ContextError("lifecycle_invalid", "history artifact lacks retirement metadata", {"path": row["path"]}, EXIT_INTEGRITY)
            row[key] = fm[key]
        if "superseded_by" in fm:
            row["superseded_by"] = fm["superseded_by"]
    for key in metadata.get("projection_fields", []):
        if key in fm:
            row[key] = fm[key]
    return row


def _validate_strict_lifecycle(
    frontmatter: dict[str, Any],
    state: str,
    path: str,
    descriptor: dict[str, Any] | None = None,
) -> None:
    reason = frontmatter.get("retired_reason")
    lifecycle_fields = {"retired_at", "retired_reason", "retirement_note", "superseded_by"}
    if state == "current" and lifecycle_fields & set(frontmatter):
        raise ContextError("lifecycle_invalid", "current artifact cannot carry lifecycle metadata", {"path": path}, EXIT_INTEGRITY)
    if state != "history":
        return
    if "retired_at" not in frontmatter or reason is None:
        raise ContextError("lifecycle_invalid", "history artifact lacks retirement metadata", {"path": path}, EXIT_INTEGRITY)
    profile = _descriptor_profile(descriptor)
    if profile is not None:
        recipe = profile["lifecycle"]["reasons"].get(reason)
        if recipe is None:
            raise ContextError("lifecycle_invalid", "retired_reason is not declared by the structural profile", {"path": path}, EXIT_INTEGRITY)
        missing = set(recipe["required_fields"]) - set(frontmatter)
        forbidden = set(recipe["forbidden_fields"]) & set(frontmatter)
        if missing or forbidden:
            raise ContextError(
                "lifecycle_invalid",
                "profiled lifecycle required or forbidden fields differ",
                {"path": path, "missing": sorted(missing), "forbidden": sorted(forbidden)},
                EXIT_INTEGRITY,
            )
        successor = recipe["successor"]
        has_successor = "superseded_by" in frontmatter
        if (successor == "required" and not has_successor) or (successor == "forbidden" and has_successor):
            raise ContextError("lifecycle_invalid", "profiled lifecycle successor cardinality differs", {"path": path}, EXIT_INTEGRITY)
        return
    allowed = {
        "context-observation/v1": {"invalidated", "superseded"},
        "context-decision/v1": {"withdrawn", "superseded"},
    }.get(frontmatter.get("schema"), set())
    if reason not in allowed:
        raise ContextError("lifecycle_invalid", "retired_reason is invalid for the artifact kind", {"path": path}, EXIT_INTEGRITY)
    if reason in {"invalidated", "withdrawn"}:
        note = frontmatter.get("retirement_note")
        if not isinstance(note, str) or not _substantive(note) or "\n" in note or len(note) > 500 or "superseded_by" in frontmatter:
            raise ContextError("lifecycle_invalid", "terminal retirement requires a note and cannot name a successor", {"path": path}, EXIT_INTEGRITY)
    if reason == "superseded":
        try:
            _require_context_id(frontmatter.get("superseded_by"), "superseded_by")
        except ContextError as error:
            raise ContextError("lifecycle_invalid", "superseded artifact requires a successor", {"path": path}, EXIT_INTEGRITY) from error


def _entry_row(row: dict[str, Any]) -> str:
    link = row["path"][:-3] if row["path"].endswith(".md") else row["path"]
    visible = f"- [[{link}]] — {_markdown_escape(row['title'])} — {_markdown_escape(row['summary'])}"
    return f"{visible} <!-- context-entry {compact_json(row)} -->"


def _area_row(row: dict[str, Any], label: str, summary: str) -> str:
    link = row["path"][:-3]
    return f"- [[{link}]] — {_markdown_escape(label)}: {_markdown_escape(summary)} <!-- context-area {compact_json(row)} -->"


def _area_label(area: str) -> str:
    return {
        "snapshot": "Snapshot",
        "observation": "Observation",
        "archive": "Archive",
        "decision": "Decision",
    }.get(area, area.replace("-", " ").title())


def _root_seed() -> str:
    return """---
schema: \"context-root-index/v1\"
index: true
owner: \"context-core\"
summary: \"Catalog of shared project context areas\"
---

# Context

## Areas
<!-- BEGIN CONTEXT GENERATED:areas -->
<!-- END CONTEXT GENERATED:areas -->
"""


def _area_seed(area: str, owner: str, artifact_schema: str, authority: str, summary: str, *, search_terms: Sequence[str] = (), projection_fields: Sequence[str] = ()) -> str:
    lines = [
        "---", 'schema: "context-area-index/v1"', "index: true", f"area: {compact_json(area)}", f"owner: {compact_json(owner)}",
        f"artifact_schema: {compact_json(artifact_schema)}", f"authority: {compact_json(authority)}", f"summary: {compact_json(summary)}",
    ]
    if search_terms:
        lines.append(f"search_terms: {compact_json(list(search_terms))}")
    if projection_fields:
        lines.append(f"projection_fields: {compact_json(list(projection_fields))}")
    lines.extend(["---", "", f"# {_area_label(area)}", "", "## Current", "<!-- BEGIN CONTEXT GENERATED:current -->", "<!-- END CONTEXT GENERATED:current -->"])
    if area != "snapshot":
        lines.extend(["", "## History", "<!-- BEGIN CONTEXT GENERATED:history -->", "<!-- END CONTEXT GENERATED:history -->"])
    return "\n".join(lines) + "\n"


def _builtin_area_specs() -> list[tuple[dict[str, Any], str, str]]:
    return [
        ({"area": "snapshot", "path": "context/snapshot/snapshot.index.md", "owner": "context-core", "claims": ["snapshot"], "artifact_schema": "context-snapshot/v1", "authority": "staging"}, "Snapshot", "session handoff staging"),
        ({"area": "observation", "path": "context/observation/observation.index.md", "owner": "context-core", "claims": ["observation"], "artifact_schema": "context-observation/v1", "authority": "evidence"}, "Observation", "Non-authoritative findings and evidence"),
        ({"area": "archive", "path": "context/archive/archive.index.md", "owner": "context-core", "claims": ["archive"], "artifact_schema": "context-archive/v1", "authority": "evidence"}, "Archive", "Immutable source material adopted as evidence"),
    ]


def render_root_index(seed: str, areas: Sequence[tuple[dict[str, Any], str, str]]) -> str:
    _parse_index_frontmatter(seed, "context-root-index/v1")
    rows = [_area_row(row, label, summary) for row, label, summary in sorted(areas, key=lambda item: item[0]["area"])]
    return _replace_block(seed, "areas", rows)


def _scan_area_paths(
    repo: pathlib.Path,
    area: str,
    metrics: IOMetrics | None = None,
    *,
    include_history: bool = True,
) -> Iterator[tuple[pathlib.Path, str]]:
    root = _ensure_contained(repo, f"context/{area}")
    if metrics:
        metrics.artifact_directory_lists += 1
    if not root.is_dir():
        return
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.name == "retired":
                retired = _ensure_contained(repo, f"context/{area}/retired")
                if not retired.is_dir():
                    raise ContextError("path_invalid", "retired path must be a directory", {"path": f"context/{area}/retired"}, EXIT_INTEGRITY)
                if not include_history:
                    continue
                if metrics:
                    metrics.artifact_directory_lists += 1
                with os.scandir(retired) as historical_entries:
                    for historical in historical_entries:
                        if historical.name.endswith(".md") and not historical.name.endswith(".index.md"):
                            if historical.is_symlink():
                                raise ContextError("symlink_path", "artifact path cannot be a symlink", {"path": f"context/{area}/retired/{historical.name}"}, EXIT_INTEGRITY)
                            yield pathlib.Path(historical.path), "history"
            elif entry.name.endswith(".md") and not entry.name.endswith(".index.md"):
                if entry.is_symlink():
                    raise ContextError("symlink_path", "artifact path cannot be a symlink", {"path": f"context/{area}/{entry.name}"}, EXIT_INTEGRITY)
                yield pathlib.Path(entry.path), "current"


def render_area_index_from_repository(repo: pathlib.Path, area: str, *, repair_rows: bool = False) -> str:
    index_path = _ensure_contained(repo, f"context/{area}/{area}.index.md")
    if not index_path.is_file():
        raise ContextError("index_seed_required", "area index is missing", {"area": area}, EXIT_INTEGRITY)
    existing = index_path.read_text(encoding="utf-8")
    descriptor = parse_area_profile(existing)
    if repair_rows:
        metadata = _parse_area_index_metadata(existing)
        _extract_block(existing, "current")
        if area != "snapshot":
            _extract_block(existing, "history")
    else:
        metadata = parse_area_index(existing).frontmatter
    if metadata["area"] != area:
        raise ContextError("area_index_mismatch", "area index metadata differs from its canonical path", {"area": area}, EXIT_INTEGRITY)
    rows: dict[str, list[dict[str, Any]]] = {"current": [], "history": []}
    for path, state in _scan_area_paths(repo, area):
        rows[state].append(_entry_from_document(repo, path, metadata, state, descriptor=descriptor))
    for state in rows:
        rows[state].sort(key=lambda row: (row["created_at"], row["id"]))
    rendered = _replace_block(existing, "current", [_entry_row(row) for row in rows["current"]])
    if area != "snapshot":
        rendered = _replace_block(rendered, "history", [_entry_row(row) for row in rows["history"]])
    return rendered


def _read_index(path: pathlib.Path, metrics: IOMetrics | None) -> str:
    if metrics:
        metrics.index_opens += 1
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise ContextError("index_stale", "index path is missing", {"path": path.as_posix()}, EXIT_INTEGRITY) from error
    if metrics:
        metrics.index_read_bytes += len(raw)
    return raw.decode("utf-8")


def _query_tokens(query: str) -> list[str]:
    normalized = normalized_key(query)
    return re.findall(r"[\w]+(?:[.-][\w]+)*", normalized, flags=re.UNICODE)


def _score_entry_details(row: dict[str, Any], query: str) -> tuple[int, int, int]:
    query_normal = normalized_key(query.strip())
    if not query_normal:
        return 0, 0, 0
    tokens = list(dict.fromkeys(_query_tokens(query)))
    title = normalized_key(row.get("title", ""))
    summary = normalized_key(row.get("summary", ""))
    path = normalized_key(row.get("path", ""))
    terms = [normalized_key(term) for term in row.get("terms", [])]
    exact_id = query_normal == normalized_key(row.get("id", ""))
    score = 100 if exact_id else 0
    if query_normal and query_normal in title:
        score += 40
    if query_normal and query_normal in summary:
        score += 10
    if query_normal in terms:
        score += 12
    matched_tokens = 0
    strong_tokens = 0
    for token in tokens:
        token_score = 0
        if token in title:
            token_score = max(token_score, 8)
        if any(token in term for term in terms):
            token_score = max(token_score, 6)
        if token in summary:
            token_score = max(token_score, 3)
        if token in path:
            token_score = max(token_score, 1)
        if token_score:
            matched_tokens += 1
            score += token_score
            if token_score > 1:
                strong_tokens += 1
    if exact_id:
        matched_tokens = max(matched_tokens, len(tokens))
        strong_tokens = max(strong_tokens, len(tokens))
    searchable = " ".join([title, summary, path, *terms])
    if tokens and all(token in searchable for token in tokens):
        score += 10
    return score, matched_tokens, strong_tokens


def score_entry(row: dict[str, Any], query: str) -> int:
    return _score_entry_details(row, query)[0]


def _fallback_entries(
    repo: pathlib.Path,
    area_row: dict[str, Any],
    metrics: IOMetrics | None,
    *,
    include_history: bool,
    maximum: int,
) -> tuple[list[dict[str, Any]], int]:
    descriptor: dict[str, Any] | None = None
    try:
        index_text = _ensure_contained(repo, area_row["path"]).read_text(encoding="utf-8")
        metadata = _parse_area_index_metadata(index_text)
        descriptor = parse_area_profile(index_text)
    except (ContextError, OSError, UnicodeError):
        metadata = {
            "area": area_row["area"], "owner": area_row["owner"], "artifact_schema": area_row["artifact_schema"],
            "authority": area_row["authority"], "projection_fields": [],
        }
    candidates = sorted(
        (path.relative_to(repo).as_posix(), path, state)
        for path, state in _scan_area_paths(
            repo,
            area_row["area"],
            metrics,
            include_history=include_history,
        )
    )
    entries = [
        _entry_from_document(repo, path, metadata, state, metrics, descriptor)
        for _, path, state in candidates[:maximum]
    ]
    return entries, len(candidates)


def _unindexed_entries(
    repo: pathlib.Path,
    area_row: dict[str, Any],
    area_index: AreaIndex,
    descriptor: dict[str, Any] | None,
    *,
    include_history: bool,
    maximum: int,
    metrics: IOMetrics | None,
) -> tuple[list[dict[str, Any]], int]:
    indexed_paths = {row["path"] for row in [*area_index.current, *area_index.history]}
    missing = sorted(
        (
            path.relative_to(repo).as_posix(),
            path,
            state,
        )
        for path, state in _scan_area_paths(
            repo,
            area_row["area"],
            metrics,
            include_history=include_history,
        )
        if path.relative_to(repo).as_posix() not in indexed_paths
    )
    rows = [
        _entry_from_document(repo, path, area_index.frontmatter, state, metrics, descriptor)
        for _, path, state in missing[:maximum]
    ]
    return rows, len(missing)


def _recall_result(items: list[dict[str, Any]], total_matches: int, fallback: bool, warnings: Sequence[str]) -> dict[str, Any]:
    omitted = max(0, total_matches - len(items))
    return {
        "items": items,
        "returned": len(items),
        "omitted": omitted,
        "truncated": omitted > 0,
        "index_fallback": fallback,
        "warnings": sorted(set(warnings)),
    }


def _fit_section_payload(
    base: dict[str, Any],
    available: dict[str, str],
    max_bytes: int,
    *,
    complete_fields: dict[str, Any],
    truncated_fields: dict[str, Any],
    too_small_code: str,
) -> dict[str, Any]:
    complete = {**base, "sections": available, **complete_fields}
    if len(canonical_json(complete).encode("utf-8")) <= max_bytes:
        return complete

    truncated: dict[str, Any] = {
        **base,
        "sections": {},
        **truncated_fields,
    }
    minimum_bytes = len(canonical_json(truncated).encode("utf-8"))
    if minimum_bytes > max_bytes:
        raise ContextError(
            too_small_code,
            "max-bytes is too small for the bounded metadata envelope",
            {"minimum_bytes": minimum_bytes, "max_bytes": max_bytes},
            EXIT_USAGE if too_small_code == "usage_invalid" else EXIT_INTEGRITY,
        )
    for name, value in available.items():
        proposed_sections = {**truncated["sections"], name: value}
        proposed = {**truncated, "sections": proposed_sections}
        if len(canonical_json(proposed).encode("utf-8")) <= max_bytes:
            truncated["sections"] = proposed_sections
            continue
        low = 0
        high = len(value)
        fitted = ""
        while low <= high:
            middle = (low + high) // 2
            prefix = value[:middle].rstrip()
            candidate = (prefix + "…") if prefix else "…"
            proposal = {**truncated, "sections": {**truncated["sections"], name: candidate}}
            if len(canonical_json(proposal).encode("utf-8")) <= max_bytes:
                fitted = candidate
                low = middle + 1
            else:
                high = middle - 1
        if fitted:
            truncated["sections"] = {**truncated["sections"], name: fitted}
        break
    return truncated


def _expanded_item(
    item: dict[str, Any],
    document: Document,
    selected_sections: Sequence[str],
    max_bytes: int,
) -> dict[str, Any]:
    schema = document.frontmatter["schema"]
    canonical_sections = tuple(_canonical_section_name(schema, name) for name in selected_sections)
    available = {
        name: value
        for name in canonical_sections
        if (value := _section_value(document.sections, schema, name)) is not None
    }
    return _fit_section_payload(
        item,
        available,
        max_bytes,
        complete_fields={},
        truncated_fields={
            "section_truncated": True,
            "full_read_hint": f"context recall --read {item['id']}",
        },
        too_small_code="recall_budget_internal",
    )


def recall_repository(
    repo: pathlib.Path,
    *,
    query: str = "",
    areas: Sequence[str] = (),
    include_history: bool = False,
    include_archive: bool = False,
    facets: Sequence[tuple[str, str]] = (),
    limit: int = 8,
    pack: bool = False,
    sections: Sequence[str] = (),
    read_ids: Sequence[str] = (),
    strict_index: bool = False,
    max_bytes: int | None = None,
    metrics: IOMetrics | None = None,
) -> dict[str, Any]:
    if not 1 <= limit <= 20 or (max_bytes is not None and not 1 <= max_bytes <= MAX_USER_BYTES):
        raise ContextError("usage_invalid", "limit or max-bytes is outside the v1 range")
    expanded = bool(pack or sections or read_ids)
    effective_max_bytes = (
        min(max_bytes, MAX_RECALL_BATCH_BYTES)
        if expanded and max_bytes is not None
        else MAX_RECALL_BATCH_BYTES
        if expanded
        else max_bytes
        if max_bytes is not None
        else MAX_STAGE1_BYTES
    )
    root_path = repo / ROOT_INDEX
    if not root_path.is_file():
        raise ContextError("context_root_missing", "context root index is missing", {"path": ROOT_INDEX}, EXIT_NOT_FOUND)
    root_text = _read_index(root_path, metrics)
    _, root_areas = parse_root_index(root_text)
    enabled = project_settings(repo)["enabled"]
    selected_areas = [
        row
        for row in root_areas
        if (row["area"] != "archive" or include_archive)
        and (not areas or row["area"] in set(areas))
        and (areas or enabled is None or row["area"] in enabled)
    ]
    all_entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    warnings: list[str] = []
    fallback = False
    fallback_read_remaining = MAX_INDEX_FALLBACK_ARTIFACT_READS
    area_indexes: dict[str, AreaIndex] = {}
    area_descriptors: dict[str, dict[str, Any] | None] = {}
    for area_row in selected_areas:
        index_path = repo / area_row["path"]
        try:
            index_text = _read_index(index_path, metrics)
            area_index = parse_area_index(index_text)
            if area_index.frontmatter["area"] != area_row["area"] or area_index.frontmatter["owner"] != area_row["owner"]:
                raise ContextError("index_stale", "area index/root catalog mismatch", exit_code=EXIT_INTEGRITY)
            area_descriptors[area_row["area"]] = _descriptor_for_area_bytes(root_text, area_row, index_text)
            area_indexes[area_row["area"]] = area_index
            rows = list(area_index.current) + (list(area_index.history) if include_history else [])
        except (ContextError, UnicodeError) as error:
            if strict_index:
                if isinstance(error, ContextError) and error.exit_code == EXIT_INTEGRITY:
                    raise error
                raise ContextError("index_stale", "area index is unreadable", {"area": area_row["area"], "cause": getattr(error, "code", type(error).__name__)}, EXIT_INTEGRITY) from error
            fallback = True
            warnings.append("area_index_invalid")
            area_descriptors[area_row["area"]] = _best_effort_area_descriptor(repo, area_row, root_text)
            rows, fallback_count = _fallback_entries(
                repo,
                area_row,
                metrics,
                include_history=include_history,
                maximum=fallback_read_remaining,
            )
            fallback_read_remaining -= len(rows)
            if fallback_count > len(rows):
                warnings.append("index_fallback_truncated")
        for row in rows:
            all_entries.append((row, area_row))
    if read_ids:
        wanted = set(read_ids)
        missing_selected = []
        for row, area_row in list(all_entries):
            if metrics:
                metrics.artifact_stats += 1
            if row["id"] in wanted and not (repo / row["path"]).is_file():
                missing_selected.append(area_row)
        missing_areas = {area_row["area"]: area_row for area_row in missing_selected}
        for area_row in [missing_areas[key] for key in sorted(missing_areas)]:
            if strict_index:
                raise ContextError("index_stale", "selected index link is missing", {"area": area_row["area"]}, EXIT_INTEGRITY)
            fallback = True
            warnings.append("selected_link_missing")
            all_entries = [(row, owner) for row, owner in all_entries if owner["area"] != area_row["area"]]
            rows, fallback_count = _fallback_entries(
                repo,
                area_row,
                metrics,
                include_history=include_history,
                maximum=fallback_read_remaining,
            )
            fallback_read_remaining -= len(rows)
            if fallback_count > len(rows):
                warnings.append("index_fallback_truncated")
            all_entries.extend((row, area_row) for row in rows)
        all_entries = [(row, area_row) for row, area_row in all_entries if row["id"] in wanted]
    query_tokens = list(dict.fromkeys(_query_tokens(query)))
    minimum_token_matches = min(2, (len(query_tokens) + 1) // 2)

    def matched(entries: Sequence[tuple[dict[str, Any], dict[str, Any]]]) -> list[tuple[dict[str, Any], dict[str, Any], int, int]]:
        output: list[tuple[dict[str, Any], dict[str, Any], int, int]] = []
        for row, area_row in entries:
            permitted = True
            for key, expected in facets:
                actual = row.get(key)
                normalized_expected = normalized_key(expected)
                if isinstance(actual, list):
                    permitted = permitted and normalized_expected in {normalized_key(str(item)) for item in actual}
                else:
                    permitted = permitted and isinstance(actual, str) and normalized_key(actual) == normalized_expected
            score, token_matches, strong_matches = _score_entry_details(row, query)
            if permitted and (
                not query
                or (score > 0 and token_matches >= minimum_token_matches)
            ):
                output.append((row, area_row, score, strong_matches))
        return output

    filtered = matched(all_entries)
    if query and not filtered and selected_areas and not fallback and not strict_index:
        remaining = MAX_INDEX_FALLBACK_ARTIFACT_READS
        missing_total = 0
        recovery_entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for area_row in selected_areas:
            area = area_row["area"]
            rows, missing_count = _unindexed_entries(
                repo,
                area_row,
                area_indexes[area],
                area_descriptors.get(area),
                include_history=include_history,
                maximum=remaining,
                metrics=metrics,
            )
            missing_total += missing_count
            remaining -= len(rows)
            recovery_entries.extend((row, area_row) for row in rows)
        if missing_total:
            fallback = True
            warnings.append("index_miss_fallback")
            if missing_total > MAX_INDEX_FALLBACK_ARTIFACT_READS:
                warnings.append("index_miss_fallback_truncated")
            filtered = matched(recovery_entries)
    if query and any(item[3] for item in filtered):
        filtered = [item for item in filtered if item[3] > 0]
    filtered.sort(key=lambda item: item[0]["id"])
    filtered.sort(key=lambda item: item[0]["created_at"], reverse=True)
    filtered.sort(key=lambda item: item[2], reverse=True)
    candidates = filtered[:limit]
    stage1_items: list[dict[str, Any]] = []
    for row, area_row, score, _ in candidates:
        item = {
            "id": row["id"], "kind": area_row["area"], "state": row["state"], "title": row["title"],
            "summary": row["summary"], "path": row["path"], "authority": area_row["authority"], "score": score,
        }
        for projection in area_indexes.get(area_row["area"], AreaIndex({}, [], [], "")).frontmatter.get("projection_fields", []):
            if projection in row:
                item[projection] = row[projection]
        stage1_items.append(item)
    total_matches = len(filtered)
    output: list[dict[str, Any]] = []
    if expanded:
        for item in stage1_items:
            minimum = {
                **item,
                "sections": {},
                "section_truncated": True,
                "full_read_hint": f"context recall --read {item['id']}",
            }
            if len(canonical_json(_recall_result(output + [minimum], total_matches, fallback, warnings)).encode("utf-8")) > effective_max_bytes:
                break
            path = repo / item["path"]
            try:
                document = parse_document(
                    _read_artifact_text(path, metrics),
                    area_descriptors.get(item["kind"]),
                )
            except FileNotFoundError:
                continue
            warnings.extend(warning["code"] for warning in document.warnings)
            selected_sections = sections or tuple(
                _canonical_section_name(document.frontmatter["schema"], name)
                for name in document.sections
            )
            result_with_placeholder = _recall_result(output + [{}], total_matches, fallback, warnings)
            result_overhead = len(canonical_json(result_with_placeholder).encode("utf-8")) - len(canonical_json({}).encode("utf-8"))
            item_budget = min(MAX_SECTION_ITEM_BYTES, effective_max_bytes - result_overhead)
            expanded_item = _expanded_item(item, document, selected_sections, item_budget)
            if len(canonical_json(_recall_result(output + [expanded_item], total_matches, fallback, warnings)).encode("utf-8")) > effective_max_bytes:
                raise ContextError("recall_budget_internal", "expanded recall item did not fit its calculated byte budget", exit_code=EXIT_INTEGRITY)
            output.append(expanded_item)
    else:
        output = stage1_items
        while output and len(canonical_json(_recall_result(output, total_matches, fallback, warnings)).encode("utf-8")) > effective_max_bytes:
            output.pop()
    result = _recall_result(output, total_matches, fallback, warnings)
    if metrics:
        metrics.output_bytes += len(canonical_json(result).encode("utf-8"))
    return result


def builtin_capability(kind: str) -> dict[str, Any]:
    if kind == "snapshot":
        return {
            "schema": "context-owner-capability/v1", "owner": "context-core", "kind": "snapshot",
            "artifact_schema": "context-snapshot/v1", "authority": "staging",
            "claim_surface": {"type": "agent_skill", "name": "bobbin:snapshot", "operation": "claim"},
            "claim_rule": "An unfinished-session handoff is requested directly or by the user's standing project recording policy",
            "claim_assertions": ["handoff_requested", "unfinished_context_present"],
            "draft_fields": {
                "required": {
                    "current_context": {"type": "string", "min_chars": 1, "max_chars": 1200},
                    "open_items": {"type": "string_list", "min_items": 1, "max_items": 8, "max_item_chars": 240},
                    "next_steps": {"type": "string_list", "min_items": 1, "max_items": 8, "max_item_chars": 240},
                },
                "optional": {
                    "decided": {"type": "string_list", "max_items": 8, "max_item_chars": 240},
                    "refs": {"type": "string_list", "max_items": 8, "max_item_chars": 500},
                    "capture_candidates": {"type": "string_list", "max_items": 8, "max_item_chars": 240},
                    "anchors": {"type": "string_list", "format": "context_id", "max_items": 12, "max_item_chars": 36},
                },
            },
        }
    if kind == "observation":
        return {
            "schema": "context-owner-capability/v1", "owner": "context-core", "kind": "observation",
            "artifact_schema": "context-observation/v1", "authority": "evidence",
            "claim_surface": {"type": "agent_skill", "name": "bobbin:observation", "operation": "claim"},
            "claim_rule": "The finding or evidence can be reused in later investigation or judgment",
            "claim_assertions": ["reusable_observation", "evidence_present"],
            "lifecycle_operations": {"same_claim": {"surface": {"type": "agent_skill", "name": "bobbin:observation", "operation": "same_claim"}, "rule": "The successor OBS corrects or more accurately carries forward the predecessor's same observation claim", "assertions": ["same_semantic_claim"]}},
            "draft_fields": {
                "required": {
                    "observation": {"type": "string", "min_chars": 1, "max_chars": 1200},
                    "evidence": {"type": "string_list", "min_items": 1, "max_items": 6, "max_item_chars": 500},
                },
                "optional": {
                    "impact": {"type": "string", "max_chars": 800},
                    "current_handling": {"type": "string", "max_chars": 800},
                    "followup_conditions": {"type": "string_list", "max_items": 8, "max_item_chars": 240},
                },
            },
        }
    if kind == "archive":
        return {
            "schema": "context-owner-capability/v1", "owner": "context-core", "kind": "archive",
            "artifact_schema": "context-archive/v1", "authority": "evidence",
            "claim_surface": {"type": "agent_skill", "name": "bobbin:archive", "operation": "claim"},
            "claim_rule": "The immutable source material has been adopted as evidence for a durable context record",
            "claim_assertions": ["source_adopted_as_evidence", "immutable_original_present"],
            "lifecycle_operations": {},
            "draft_fields": {
                "required": {
                    "content": {
                        "type": "string",
                        "min_chars": 1,
                        "max_chars": MAX_ARCHIVE_CONTENT_CODEPOINTS,
                    },
                },
                "optional": {},
            },
        }
    raise ContextError("owner_unavailable", "built-in capability is unavailable", {"kind": kind}, EXIT_CONFLICT)


def capabilities_result() -> dict[str, Any]:
    return {
        "schema": "context-owner-capabilities/v1",
        "owners": [
            builtin_capability("snapshot"),
            builtin_capability("observation"),
            builtin_capability("archive"),
        ],
    }


def _capability_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and value.get("schema") == "context-owner-capabilities/v1":
        owners = value.get("owners")
    elif isinstance(value, list):
        owners = value
    else:
        raise ContextError("capability_invalid", "capabilities must use context-owner-capabilities/v1", exit_code=EXIT_CONFLICT)
    if not isinstance(owners, list):
        raise ContextError("capability_invalid", "capability owners must be an array", exit_code=EXIT_CONFLICT)
    output: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for capability in owners:
        if not isinstance(capability, dict) or capability.get("schema") != "context-owner-capability/v1":
            raise ContextError("capability_invalid", "owner capability envelope is invalid", exit_code=EXIT_CONFLICT)
        owner = capability.get("owner")
        kind = capability.get("kind")
        surface = capability.get("claim_surface")
        authority = capability.get("authority")
        descriptor_digest = capability.get("descriptor_digest")
        if (
            not isinstance(owner, str)
            or not isinstance(kind, str)
            or not isinstance(capability.get("artifact_schema"), str)
            or authority not in {"staging", "evidence", "provisional", "authoritative"}
            or (
                descriptor_digest is not None
                and not re.fullmatch(r"sha256:[0-9a-f]{64}", str(descriptor_digest))
            )
            or (authority == "provisional" and descriptor_digest is None)
            or not isinstance(surface, dict)
            or surface.get("type") != "agent_skill"
            or surface.get("operation") != "claim"
            or not isinstance(surface.get("name"), str)
        ):
            raise ContextError("capability_invalid", "owner capability identity or host surface is invalid", exit_code=EXIT_CONFLICT)
        identity = (owner, kind)
        if identity in identities or any(item["kind"] == kind for item in output):
            raise ContextError("owner_conflict", "more than one capability claims a kind", {"kind": kind}, EXIT_CONFLICT)
        identities.add(identity)
        output.append(capability)
    return output


def _candidate_batch_bytes(batch: Any, candidates: list[dict[str, Any]]) -> int:
    envelope = batch if isinstance(batch, dict) else {
        "schema": "context-capture-batch/v1",
        "audit_count": 1,
        "candidates": candidates,
    }
    return len(canonical_json(envelope).encode("utf-8"))


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


def _candidate_batch_limit(candidates: Any) -> int:
    if (
        isinstance(candidates, list)
        and len(candidates) == 1
        and isinstance(candidates[0], dict)
        and candidates[0].get("requested_kind") == "archive"
        and candidates[0].get("specialized_kinds") == ["archive"]
        and candidates[0].get("fallback_kind") is None
    ):
        return MAX_ARCHIVE_ENVELOPE_BYTES
    return MAX_CANDIDATE_BYTES


def validate_candidate_batch(batch: Any, capabilities: Any) -> list[dict[str, Any]]:
    if isinstance(batch, dict):
        expected = {"schema", "audit_count", "candidates"}
        if (
            set(batch) != expected
            or batch.get("schema") != "context-capture-batch/v1"
            or type(batch.get("audit_count")) is not int
            or not isinstance(batch.get("candidates"), list)
        ):
            raise ContextError(
                "candidate_invalid",
                "candidate batch envelope is invalid",
                {"missing": sorted(expected - set(batch)), "extra": sorted(set(batch) - expected)},
                EXIT_CONFLICT,
            )
        candidates = batch["candidates"]
        if batch["audit_count"] != 1:
            raise ContextError("audit_repeated", "a semantic milestone may be audited at most once", exit_code=EXIT_CONFLICT)
    elif isinstance(batch, list):
        candidates = batch
    else:
        raise ContextError("candidate_invalid", "candidate batch must be an array or v1 envelope", exit_code=EXIT_CONFLICT)
    if len(candidates) > 8:
        raise ContextError("candidate_batch_too_large", "candidate batch exceeds the v1 count budget", {"maximum": 8}, EXIT_CONFLICT)
    batch_bytes = _candidate_batch_bytes(batch, candidates)
    batch_limit = _candidate_batch_limit(candidates)
    if batch_bytes > batch_limit:
        raise ContextError(
            "candidate_batch_too_large",
            "candidate batch exceeds its bounded envelope budget",
            _byte_size_details(batch_bytes, batch_limit),
            EXIT_CONFLICT,
        )
    capability_by_kind = {item["kind"]: item for item in _capability_list(capabilities)}
    required = {
        "schema", "candidate_id", "title", "claim", "summary", "captured_from", "requested_kind",
        "specialized_kinds", "fallback_kind", "owner_inputs",
    }
    candidate_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("schema") != "context-capture-candidate/v1" or required - set(candidate):
            raise ContextError("candidate_invalid", "candidate envelope is incomplete", exit_code=EXIT_CONFLICT)
        removed = sorted(REMOVED_CANDIDATE_FIELDS & set(candidate))
        if removed:
            raise ContextError(
                "schema_removed_field",
                "semantic identity surrogate fields were removed from capture candidates",
                {"fields": removed},
                EXIT_CONFLICT,
            )
        identifier = candidate.get("candidate_id")
        if not isinstance(identifier, str) or not re.fullmatch(r"cand_[0-9a-f]{32}", identifier):
            raise ContextError("candidate_invalid", "candidate_id is invalid")
        if identifier in candidate_ids:
            raise ContextError("candidate_invalid", "candidate_id is duplicated", {"candidate_id": identifier}, EXIT_CONFLICT)
        candidate_ids.add(identifier)
        if not _substantive(candidate.get("title")) or len(candidate["title"]) > 120 or "\n" in candidate["title"]:
            raise ContextError("candidate_invalid", "candidate title is invalid")
        claim = candidate.get("claim")
        if not _substantive(claim):
            raise ContextError("candidate_invalid", "candidate claim is invalid")
        claim_limit = (
            MAX_ARCHIVE_CONTENT_CODEPOINTS
            if candidate.get("requested_kind") == "archive"
            else MAX_PRIMARY_CLAIM_CODEPOINTS
        )
        if len(claim) > claim_limit:
            raise ContextError(
                "candidate_invalid",
                "candidate claim exceeds its kind-specific codepoint limit",
                {"field": "claim", **_codepoint_size_details(len(claim), claim_limit)},
                EXIT_CONFLICT,
            )
        if not _substantive(candidate.get("summary")) or len(candidate["summary"]) > 280 or "\n" in candidate["summary"]:
            raise ContextError("candidate_invalid", "candidate summary is invalid")
        if candidate.get("captured_from") not in {"conversation", "workspace", "manual", "import"}:
            raise ContextError("candidate_invalid", "candidate provenance is invalid")
        requested = candidate.get("requested_kind")
        specialized = candidate.get("specialized_kinds")
        fallback = candidate.get("fallback_kind")
        if requested is not None and not isinstance(requested, str):
            raise ContextError("candidate_invalid", "requested_kind must be a string or null")
        if not isinstance(specialized, list) or len(specialized) > 2 or len(specialized) != len(set(specialized)) or not all(isinstance(item, str) for item in specialized):
            raise ContextError("candidate_invalid", "specialized_kinds is invalid")
        if fallback not in {None, "observation", "snapshot"}:
            raise ContextError("candidate_invalid", "fallback_kind is invalid")
        evidence = candidate.get("evidence", [])
        if not isinstance(evidence, list) or len(evidence) > 2 or any(not _substantive(item) or len(item) > 240 for item in evidence):
            raise ContextError("candidate_invalid", "candidate evidence is invalid")
        owner_inputs = candidate.get("owner_inputs")
        if not isinstance(owner_inputs, dict):
            raise ContextError("candidate_invalid", "owner_inputs must be an object")
        relevant = set(specialized) | ({requested} if requested else set()) | ({fallback} if fallback else set())
        if set(owner_inputs) - relevant:
            raise ContextError("candidate_invalid", "owner_inputs contains an unrouted kind", {"kinds": sorted(set(owner_inputs) - relevant)})
        for kind, owner_input in owner_inputs.items():
            owner_input_bytes = len(canonical_json(owner_input).encode("utf-8"))
            owner_input_limit = MAX_ARCHIVE_ENVELOPE_BYTES if kind == "archive" else MAX_OWNER_INPUT_BYTES
            if owner_input_bytes > owner_input_limit:
                raise ContextError(
                    "candidate_too_large",
                    "owner input exceeds its kind-specific byte budget",
                    {"kind": kind, **_byte_size_details(owner_input_bytes, owner_input_limit)},
                    EXIT_CONFLICT,
                )
            capability = capability_by_kind.get(kind)
            if capability is not None and capability["owner"] == "context-core":
                _validate_builtin_candidate_primary(candidate, kind, owner_input)
    return candidates


def _claim_result_map(results: Any) -> dict[tuple[str, str], dict[str, Any]]:
    if isinstance(results, dict) and results.get("schema") == "context-owner-results/v1":
        values = results.get("results")
    else:
        values = results
    if not isinstance(values, list):
        raise ContextError("owner_result_invalid", "claim results must be an array", exit_code=EXIT_CONFLICT)
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for result in values:
        if not isinstance(result, dict):
            raise ContextError("owner_result_invalid", "claim result is not an object", exit_code=EXIT_CONFLICT)
        key = (result.get("candidate_id"), result.get("target_kind"))
        if not all(isinstance(item, str) for item in key) or key in output:
            raise ContextError("owner_conflict", "claim result is duplicated for candidate/kind", exit_code=EXIT_CONFLICT)
        output[key] = result
    return output


def route_candidates(batch: Any, capabilities: Any, claim_results: Any, *, repo: pathlib.Path | None = None) -> dict[str, Any]:
    candidates = validate_candidate_batch(batch, capabilities)
    batch_bytes = _candidate_batch_bytes(batch, candidates)
    capability_by_kind = {item["kind"]: item for item in _capability_list(capabilities)}
    enabled = project_settings(repo)["enabled"] if repo is not None else None
    if enabled is not None:
        capability_by_kind = {kind: value for kind, value in capability_by_kind.items() if kind in enabled}
    results = _claim_result_map(claim_results)
    routes: list[dict[str, Any]] = []
    for candidate in candidates:
        requested = candidate["requested_kind"]
        ordered = [requested] if requested else list(candidate["specialized_kinds"])
        if enabled is not None and ordered and not any(kind in enabled for kind in ordered):
            routes.append({"candidate_id": candidate["candidate_id"], "status": "skipped", "reason": "feature_disabled"})
            continue
        available = [kind for kind in ordered if kind in capability_by_kind]
        if requested and not available:
            routes.append({"candidate_id": candidate["candidate_id"], "status": "owner_unavailable", "reason": "requested_owner_unavailable"})
            continue
        evaluated: list[tuple[str, dict[str, Any]]] = []
        for kind in available:
            result = results.get((candidate["candidate_id"], kind))
            if result is None:
                continue
            capability = capability_by_kind[kind]
            embedded = next((item for item in result.get("semantic_inputs", []) if item.get("operation") == "claim"), None)
            if (
                result.get("schema") != "context-owner-result/v1"
                or result.get("result_type") != "claim"
                or result.get("owner") != capability["owner"]
                or result.get("capability_digest") != canonical_digest(capability)
                or embedded is None
                or embedded.get("value") != candidate
                or embedded.get("input_digest") != canonical_digest(candidate)
            ):
                raise ContextError("claim_result_mismatch", "host-collected owner result differs from candidate/capability", exit_code=EXIT_CONFLICT)
            validate_owner_result(result, capability)
            evaluated.append((kind, result))
        clarifications = [(kind, result) for kind, result in evaluated if result.get("decision") == "needs_clarification"]
        claims = [(kind, result) for kind, result in evaluated if result.get("decision") == "claim"]
        if clarifications:
            kind, result = clarifications[0]
            routes.append({"candidate_id": candidate["candidate_id"], "status": "needs_clarification", "owner": result["owner"], "target_kind": kind, "reason": result["reason"]})
            continue
        if len(claims) > 1:
            routes.append({"candidate_id": candidate["candidate_id"], "status": "owner_conflict", "reason": "multiple_specialized_owners_claimed"})
            continue
        if len(claims) == 1:
            kind, result = claims[0]
            reason = "requested_owner" if requested else ("fallback_owner" if kind == candidate.get("fallback_kind") else "specialized_owner")
            routes.append({
                "candidate_id": candidate["candidate_id"], "status": "proposed",
                "owner": result["owner"], "target_kind": kind, "authority": capability_by_kind[kind]["authority"],
                "reason": reason, "owner_result_digest": canonical_digest(result),
            })
            continue
        if requested:
            declined = next((result for _, result in evaluated if result.get("decision") == "decline"), None)
            routes.append({"candidate_id": candidate["candidate_id"], "status": "skipped", "reason": "owner_decline" if declined else "owner_unavailable"})
            continue
        if available and len(evaluated) != len(available):
            routes.append({"candidate_id": candidate["candidate_id"], "status": "owner_unavailable", "reason": "specialized_owner_result_missing"})
            continue
        fallback = candidate.get("fallback_kind")
        fallback_result = results.get((candidate["candidate_id"], fallback)) if fallback else None
        if fallback and fallback in capability_by_kind and fallback_result is not None:
            capability = capability_by_kind[fallback]
            embedded = next((item for item in fallback_result.get("semantic_inputs", []) if item.get("operation") == "claim"), None)
            if fallback_result.get("owner") != capability["owner"] or fallback_result.get("capability_digest") != canonical_digest(capability) or embedded is None or embedded.get("value") != candidate:
                raise ContextError("claim_result_mismatch", "fallback result differs from candidate/capability", exit_code=EXIT_CONFLICT)
            validate_owner_result(fallback_result, capability)
            if fallback_result.get("decision") == "claim":
                routes.append({
                    "candidate_id": candidate["candidate_id"], "status": "proposed",
                    "owner": fallback_result["owner"], "target_kind": fallback, "authority": capability["authority"],
                    "reason": "fallback_owner", "owner_result_digest": canonical_digest(fallback_result),
                })
                continue
        routes.append({"candidate_id": candidate["candidate_id"], "status": "skipped", "reason": "no_owner_claim"})
    return {
        "schema": "context-route-result/v1", "routes": routes,
        "conflicts": [item for item in routes if item["status"] == "owner_conflict"],
        "skipped": [item for item in routes if item["status"] == "skipped"],
        "canonical_bytes": batch_bytes,
        "router_owner_process_invocations": 0, "cache_probe_count": 0, "alternate_runtime_count": 0,
    }


def _validate_owner_inputs(kind: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContextError("candidate_invalid", "owner input must be an object")
    capability = builtin_capability(kind)
    fields = capability["draft_fields"]
    allowed = set(fields["required"]) | set(fields["optional"])
    missing = set(fields["required"]) - set(value)
    extra = set(value) - allowed
    if missing or extra:
        raise ContextError("candidate_invalid", "owner input fields differ from capability", {"missing": sorted(missing), "extra": sorted(extra)})
    normalized: dict[str, Any] = {}
    for name, raw in value.items():
        spec = fields["required"].get(name) or fields["optional"][name]
        if spec["type"] == "string":
            if not _substantive(raw) or len(raw) > spec["max_chars"]:
                raise ContextError("candidate_invalid", f"owner input {name} is not substantive")
            normalized[name] = nfc(raw.strip())
        elif spec["type"] == "string_list":
            items = _string_list(
                raw,
                name,
                required=bool(spec.get("min_items")),
                maximum=spec["max_items"],
                item_maximum=spec["max_item_chars"],
            )
            if spec.get("format") == "context_id":
                for identifier in items:
                    _require_context_id(identifier, name)
            normalized[name] = items
        else:
            raise ContextError("candidate_invalid", "unsupported capability field type", {"field": name})
    return normalized


def _validate_builtin_candidate_primary(
    candidate: dict[str, Any],
    kind: str,
    owner_input: Any,
) -> dict[str, Any]:
    normalized = _validate_owner_inputs(kind, owner_input)
    primary_field = {
        "snapshot": "current_context",
        "observation": "observation",
        "archive": "content",
    }[kind]
    if candidate.get("claim") != normalized[primary_field]:
        raise ContextError(
            "candidate_primary_claim_mismatch",
            "candidate claim must equal the built-in owner's actual primary field",
            {
                "kind": kind,
                "claim_field": "claim",
                "owner_primary_field": f"owner_inputs.{kind}.{primary_field}",
            },
            EXIT_CONFLICT,
        )
    return normalized


def direct_candidate(
    kind: str,
    *,
    title: str,
    summary: str,
    captured_from: str,
    owner_inputs: dict[str, Any],
    source_refs: Sequence[str] = (),
    tags: Sequence[str] = (),
    search_terms: Sequence[str] = (),
    kind_hint: str | None = None,
) -> dict[str, Any]:
    if kind not in BUILTIN_AREAS:
        raise ContextError("owner_unavailable", "direct candidate kind is unavailable", {"kind": kind}, EXIT_CONFLICT)
    if not _substantive(title) or "\n" in title or len(title) > 120:
        raise ContextError("candidate_invalid", "candidate title is invalid")
    if not _substantive(summary) or "\n" in summary or len(summary) > 280:
        raise ContextError("candidate_invalid", "candidate summary is invalid")
    if captured_from not in {"conversation", "workspace", "manual", "import"}:
        raise ContextError("candidate_invalid", "candidate captured_from is invalid")
    if kind_hint is not None and (kind != "observation" or kind_hint != "decision"):
        raise ContextError("candidate_invalid", "kind_hint is only supported for decision-like observations")
    normalized_inputs = _validate_owner_inputs(kind, owner_inputs)
    owner_input_bytes = len(canonical_json(normalized_inputs).encode("utf-8"))
    owner_input_limit = MAX_ARCHIVE_ENVELOPE_BYTES if kind == "archive" else MAX_OWNER_INPUT_BYTES
    if owner_input_bytes > owner_input_limit:
        raise ContextError(
            "candidate_too_large",
            "owner input exceeds its kind-specific byte budget",
            {"kind": kind, **_byte_size_details(owner_input_bytes, owner_input_limit)},
            EXIT_CONFLICT,
        )
    primary = normalized_inputs[{
        "snapshot": "current_context",
        "observation": "observation",
        "archive": "content",
    }[kind]]
    normalized_source_refs = _string_list(list(source_refs), "source_refs", maximum=12, item_maximum=500)
    if kind == "archive" and not normalized_source_refs:
        raise ContextError("candidate_invalid", "archive requires at least one source_ref", exit_code=EXIT_CONFLICT)
    candidate: dict[str, Any] = {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_" + uuid.uuid4().hex,
        "title": nfc(title.strip()),
        "claim": primary,
        "summary": nfc(summary.strip()),
        "captured_from": captured_from,
        "requested_kind": kind,
        "specialized_kinds": [kind],
        "fallback_kind": None,
        "source_refs": normalized_source_refs,
        "tags": _string_list(list(tags), "tags", maximum=12, item_maximum=40),
        "search_terms": _string_list(list(search_terms), "search_terms", maximum=12, item_maximum=40),
        "owner_inputs": {kind: normalized_inputs},
    }
    if kind_hint is not None:
        candidate["kind_hint"] = kind_hint
    candidate_bytes = len(canonical_json(candidate).encode("utf-8"))
    candidate_limit = MAX_ARCHIVE_ENVELOPE_BYTES if kind == "archive" else MAX_CANDIDATE_BYTES
    if candidate_bytes > candidate_limit:
        raise ContextError(
            "candidate_too_large",
            "candidate exceeds its kind-specific byte budget",
            _byte_size_details(candidate_bytes, candidate_limit),
            EXIT_CONFLICT,
        )
    return candidate


def _validate_attestation(attestation: dict[str, Any], operation: str, input_value: dict[str, Any], expected_assertions: set[str]) -> None:
    if (
        not isinstance(attestation, dict)
        or attestation.get("schema") != "context-semantic-attestation/v1"
        or attestation.get("operation") != operation
        or attestation.get("input_schema") != input_value.get("schema")
        or attestation.get("input_digest") != canonical_digest(input_value)
    ):
        raise ContextError("semantic_attestation_invalid", "attestation is not bound to the exact semantic input", exit_code=EXIT_CONFLICT)
    assertions = attestation.get("assertions")
    if not isinstance(assertions, list) or {item.get("name") for item in assertions} != expected_assertions:
        raise ContextError("semantic_attestation_invalid", "attestation assertions differ from the owner capability", exit_code=EXIT_CONFLICT)
    for assertion in assertions:
        pointers = assertion.get("evidence_pointers")
        if assertion.get("value") is not True or not isinstance(pointers, list) or not 1 <= len(pointers) <= 4:
            raise ContextError("semantic_attestation_invalid", "attestation assertion is invalid", exit_code=EXIT_CONFLICT)
        for pointer in pointers:
            _json_pointer(input_value, pointer)
    if operation == "same_claim":
        pointers = {pointer for assertion in assertions for pointer in assertion["evidence_pointers"]}
        if not {"/predecessor/primary_claim", "/successor/primary_claim"}.issubset(pointers):
            raise ContextError("semantic_attestation_invalid", "same_claim must cite both primary claims", exit_code=EXIT_CONFLICT)


def _list_section(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _list_section_items(value: str | None) -> list[str]:
    if not isinstance(value, str):
        return []
    return [
        line[2:].strip() if line.startswith("- ") else line.strip()
        for line in value.splitlines()
        if line.strip()
    ]


def _list_section_matches(value: str | None, items: Sequence[str]) -> bool:
    return isinstance(value, str) and _list_section_items(value) == list(items)


def _validate_claim_draft(kind: str, candidate: dict[str, Any], draft: dict[str, Any]) -> None:
    owner_inputs = _validate_owner_inputs(kind, candidate.get("owner_inputs", {}).get(kind))
    document = parse_document(draft.get("content", ""))
    frontmatter = document.frontmatter
    if (
        frontmatter.get("schema") != builtin_capability(kind)["artifact_schema"]
        or frontmatter.get("title") != candidate.get("title")
        or frontmatter.get("summary") != candidate.get("summary")
        or frontmatter.get("captured_from") != candidate.get("captured_from")
        or frontmatter.get("source_refs", []) != candidate.get("source_refs", [])
        or frontmatter.get("tags", []) != candidate.get("tags", [])
        or frontmatter.get("search_terms", []) != candidate.get("search_terms", [])
    ):
        raise ContextError("claim_result_mismatch", "claim draft envelope differs from embedded candidate", exit_code=EXIT_CONFLICT)
    if kind == "snapshot":
        expected = {
            "Current context": owner_inputs["current_context"],
            "Open items": owner_inputs["open_items"],
            "Next steps": owner_inputs["next_steps"],
        }
        optional = (("decided", "Decided"), ("refs", "References"), ("capture_candidates", "Capture candidates"))
        if frontmatter.get("anchors", []) != owner_inputs.get("anchors", []):
            raise ContextError("claim_result_mismatch", "snapshot anchors differ from embedded candidate", exit_code=EXIT_CONFLICT)
    elif kind == "observation":
        primary = owner_inputs["observation"]
        expected = {"Observation": primary, "Evidence": owner_inputs["evidence"]}
        optional = (("impact", "Impact"), ("current_handling", "Current handling"), ("followup_conditions", "Follow-up conditions"))
        if frontmatter.get("kind_hint") != candidate.get("kind_hint"):
            raise ContextError("claim_result_mismatch", "observation kind_hint differs from embedded candidate", exit_code=EXIT_CONFLICT)
    else:
        expected = {"Content": owner_inputs["content"]}
        optional = ()
        if not candidate.get("source_refs"):
            raise ContextError("claim_result_mismatch", "archive requires source_refs", exit_code=EXIT_CONFLICT)
    for field, section in optional:
        value = owner_inputs.get(field)
        if value:
            expected[section] = value
    if {_canonical_section_name(frontmatter["schema"], name) for name in document.sections} != set(expected) or any(
        not (_list_section_matches(_section_value(document.sections, frontmatter["schema"], section), value) if isinstance(value, list) else _section_value(document.sections, frontmatter["schema"], section) == value)
        for section, value in expected.items()
    ):
        raise ContextError("claim_result_mismatch", "claim draft sections differ from embedded owner inputs", exit_code=EXIT_CONFLICT)


def draft_owner_result(
    candidate: dict[str, Any],
    attestation: dict[str, Any],
    *,
    filename: str | None = None,
    identifier: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    if candidate.get("schema") != "context-capture-candidate/v1" or candidate.get("requested_kind") not in BUILTIN_AREAS:
        raise ContextError("candidate_invalid", "direct candidate envelope is invalid")
    kind = candidate["requested_kind"]
    if candidate.get("specialized_kinds") != [kind] or candidate.get("fallback_kind") is not None:
        raise ContextError("candidate_invalid", "direct candidate routing fields are invalid")
    inputs = candidate.get("owner_inputs")
    if not isinstance(inputs, dict) or set(inputs) != {kind}:
        raise ContextError("candidate_invalid", "candidate owner_inputs must contain only the target kind")
    owner_inputs = _validate_builtin_candidate_primary(candidate, kind, inputs[kind])
    capability = builtin_capability(kind)
    _validate_attestation(attestation, "claim", candidate, set(capability["claim_assertions"]))
    created_at = _timestamp(now)
    identifier = identifier or new_context_id()
    _require_context_id(identifier)
    relative = f"context/{kind}/{validate_filename(filename) if filename else natural_filename(candidate['title'])}"
    frontmatter: dict[str, Any] = {
        "schema": capability["artifact_schema"],
        "id": identifier,
        "title": candidate["title"],
        "summary": candidate["summary"],
        "created_at": created_at,
        "captured_from": candidate["captured_from"],
    }
    for field in ("source_refs", "tags", "search_terms"):
        if candidate.get(field):
            frontmatter[field] = list(candidate[field])
    if kind == "snapshot":
        frontmatter["updated_at"] = created_at
        if owner_inputs.get("anchors"):
            frontmatter["anchors"] = owner_inputs["anchors"]
        sections = {
            "Current context": owner_inputs["current_context"],
            "Open items": _list_section(owner_inputs["open_items"]),
            "Next steps": _list_section(owner_inputs["next_steps"]),
        }
        optional_sections = (("decided", "Decided"), ("refs", "References"), ("capture_candidates", "Capture candidates"))
    elif kind == "observation":
        primary_claim = owner_inputs["observation"]
        if candidate.get("kind_hint") == "decision":
            frontmatter["kind_hint"] = "decision"
        sections = {"Observation": primary_claim, "Evidence": _list_section(owner_inputs["evidence"])}
        optional_sections = (("impact", "Impact"), ("current_handling", "Current handling"), ("followup_conditions", "Follow-up conditions"))
    else:
        sections = {"Content": owner_inputs["content"]}
        optional_sections = ()
    for field, section in optional_sections:
        value = owner_inputs.get(field)
        if value:
            sections[section] = _list_section(value) if isinstance(value, list) else value
    content = render_document(frontmatter, sections)
    effect_id = f"effect_create_{kind}"
    supporting = owner_inputs.get("evidence", owner_inputs.get("open_items", []))[:4]
    projection = {
        "kind": kind,
        "primary_claim": next(iter(sections.values())),
        "supporting_context": supporting,
    }
    return {
        "schema": "context-owner-result/v1",
        "result_type": "claim",
        "transition": "capture",
        "owner": "context-core",
        "target_kind": kind,
        "candidate_id": candidate["candidate_id"],
        "decision": "claim",
        "reason": "explicit direct capture",
        "capability_digest": canonical_digest(capability),
        "semantic_inputs": [{"operation": "claim", "input_schema": candidate["schema"], "input_digest": canonical_digest(candidate), "value": candidate}],
        "semantic_attestations": [attestation],
        "artifact_drafts": [{"effect_id": effect_id, "path": relative, "content": content, "semantic_projection": projection}],
        "effects": [{"effect_id": effect_id, "action": "create", "area": kind, "id": identifier, "state": "current"}],
        "proposed_plan": {"schema": "context-owner-plan/v1", "transition": "capture", "operations": [{"op": "create", "effect_id": effect_id, "area": kind, "path": relative}]},
    }


def _json_pointer(value: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ContextError("semantic_attestation_invalid", "evidence pointer must be RFC 6901")
    current = value
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as error:
                raise ContextError("semantic_attestation_invalid", "evidence pointer does not resolve", {"pointer": pointer}) from error
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise ContextError("semantic_attestation_invalid", "evidence pointer does not resolve", {"pointer": pointer})
    if current in (None, "", []):
        raise ContextError("semantic_attestation_invalid", "evidence pointer resolves to an empty value", {"pointer": pointer})
    return current


def validate_owner_result(
    result: dict[str, Any],
    capability: dict[str, Any] | None = None,
    descriptor: dict[str, Any] | None = None,
) -> None:
    if result.get("schema") != "context-owner-result/v1" or not isinstance(result.get("owner"), str):
        raise ContextError("owner_result_invalid", "owner result envelope is invalid", exit_code=EXIT_CONFLICT)
    missing = {"result_type", "transition", "target_kind", "capability_digest", "semantic_inputs", "semantic_attestations", "artifact_drafts", "effects", "proposed_plan"} - set(result)
    if missing:
        raise ContextError("owner_result_invalid", "owner result required fields are missing", {"missing": sorted(missing)}, EXIT_CONFLICT)
    kind = result["target_kind"]
    profile = _descriptor_profile(descriptor)
    if descriptor is not None and (
        result.get("owner") != descriptor.get("owner")
        or kind != descriptor.get("kind")
    ):
        raise ContextError("owner_result_invalid", "owner result differs from its registered descriptor", exit_code=EXIT_CONFLICT)
    if result["owner"] == "context-core":
        capability = builtin_capability(kind)
    if capability is not None:
        if result.get("owner") != capability.get("owner") or kind != capability.get("kind") or result["capability_digest"] != canonical_digest(capability):
            raise ContextError("capability_digest_mismatch", "owner result capability digest is stale", exit_code=EXIT_CONFLICT)
    elif not isinstance(result.get("capability_digest"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", result["capability_digest"]):
        raise ContextError("capability_digest_mismatch", "owner result capability digest is invalid", exit_code=EXIT_CONFLICT)
    inputs: dict[str, dict[str, Any]] = {}
    for item in result["semantic_inputs"]:
        operation = item.get("operation")
        if operation in inputs or item.get("input_schema") != item.get("value", {}).get("schema") or item.get("input_digest") != canonical_digest(item.get("value")):
            raise ContextError("semantic_input_invalid", "semantic input schema or digest is invalid", exit_code=EXIT_CONFLICT)
        inputs[operation] = item
    if "claim" in inputs and result["owner"] == "context-core" and kind in BUILTIN_AREAS:
        candidate = inputs["claim"]["value"]
        _validate_builtin_candidate_primary(
            candidate,
            kind,
            candidate.get("owner_inputs", {}).get(kind),
        )
    attestations: dict[str, dict[str, Any]] = {}
    for attestation in result["semantic_attestations"]:
        operation = attestation.get("operation")
        semantic_input = inputs.get(operation)
        if operation in attestations or semantic_input is None or attestation.get("schema") != "context-semantic-attestation/v1" or attestation.get("input_digest") != semantic_input["input_digest"]:
            raise ContextError("semantic_attestation_invalid", "semantic attestation is not bound to its input", exit_code=EXIT_CONFLICT)
        names: set[str] = set()
        for assertion in attestation.get("assertions", []):
            name = assertion.get("name")
            pointers = assertion.get("evidence_pointers", [])
            if name in names or assertion.get("value") is not True or not 1 <= len(pointers) <= 4:
                raise ContextError("semantic_attestation_invalid", "attestation assertion is invalid", exit_code=EXIT_CONFLICT)
            names.add(name)
            for pointer in pointers:
                _json_pointer(semantic_input["value"], pointer)
        attestations[operation] = attestation
    if result["result_type"] == "claim" and result.get("decision") in {"decline", "needs_clarification"}:
        if (
            result.get("transition") != "capture"
            or set(inputs) != {"claim"}
            or attestations
            or result.get("artifact_drafts")
            or result.get("effects")
            or result.get("proposed_plan") is not None
            or result.get("candidate_id") != inputs["claim"]["value"].get("candidate_id")
        ):
            raise ContextError("owner_result_invalid", "decline/clarification must contain only the exact claim input", exit_code=EXIT_CONFLICT)
        return
    if result["result_type"] == "claim":
        if result.get("decision") != "claim" or result.get("transition") != "capture" or "claim" not in inputs or "claim" not in attestations:
            raise ContextError("owner_result_invalid", "claim result lacks complete claim evidence", exit_code=EXIT_CONFLICT)
        if result.get("candidate_id") != inputs["claim"]["value"].get("candidate_id"):
            raise ContextError("claim_result_mismatch", "claim result candidate does not match embedded input", exit_code=EXIT_CONFLICT)
        if capability is not None:
            expected = set(capability["claim_assertions"])
            actual = {assertion["name"] for assertion in attestations["claim"]["assertions"]}
            if expected != actual:
                raise ContextError("semantic_attestation_invalid", "claim assertions do not match capability", exit_code=EXIT_CONFLICT)
            _validate_attestation(attestations["claim"], "claim", inputs["claim"]["value"], expected)
    elif result["result_type"] == "mutation":
        if "mutation_request" not in inputs or "mutation_request" in attestations:
            raise ContextError("owner_result_invalid", "mutation result lacks an unattested mutation request", exit_code=EXIT_CONFLICT)
        required_inputs = {
            "snapshot_update": {"mutation_request"},
            "observation_annotate": {"mutation_request"},
            "observation_reverify": {"mutation_request"},
            "observation_invalidate": {"mutation_request"},
            "discard": {"mutation_request"},
            "rename": {"mutation_request"},
            "observation_supersede": {"claim", "same_claim", "mutation_request"},
            "decision_fallback_import": {"claim", "same_claim", "mutation_request"},
        }.get(result["transition"])
        if required_inputs is not None and set(inputs) != required_inputs:
            raise ContextError("owner_result_invalid", "mutation semantic input set is incomplete", {"expected": sorted(required_inputs)}, EXIT_CONFLICT)
        if result["transition"] == "observation_supersede":
            if set(attestations) != {"claim", "same_claim"}:
                raise ContextError("semantic_attestation_invalid", "observation supersede requires claim and same_claim attestations", exit_code=EXIT_CONFLICT)
            _validate_attestation(attestations["claim"], "claim", inputs["claim"]["value"], set(builtin_capability("observation")["claim_assertions"]))
            _validate_attestation(attestations["same_claim"], "same_claim", inputs["same_claim"]["value"], {"same_semantic_claim"})
        if result["transition"] == "decision_fallback_import" and set(attestations) != {"claim", "same_claim"}:
            raise ContextError("semantic_attestation_invalid", "fallback import requires claim and same_claim attestations", exit_code=EXIT_CONFLICT)
    else:
        raise ContextError("owner_result_invalid", "result_type is unsupported", exit_code=EXIT_CONFLICT)
    if "claim" in inputs and result["owner"] == "context-core":
        create_ids = {effect.get("effect_id") for effect in result.get("effects", []) if effect.get("action") == "create"}
        claim_drafts = [draft for draft in result.get("artifact_drafts", []) if draft.get("effect_id") in create_ids]
        if len(claim_drafts) != 1:
            raise ContextError("claim_result_mismatch", "embedded claim must bind exactly one create draft", exit_code=EXIT_CONFLICT)
        _validate_claim_draft(kind, inputs["claim"]["value"], claim_drafts[0])
    plan = result["proposed_plan"]
    if plan.get("schema") != "context-owner-plan/v1" or plan.get("transition") != result["transition"]:
        raise ContextError("owner_result_invalid", "owner plan does not match result transition", exit_code=EXIT_CONFLICT)
    drafts = result["artifact_drafts"]
    effects = result["effects"]
    operations = plan.get("operations", [])
    for collection, label in ((drafts, "draft"), (effects, "effect"), (operations, "operation")):
        ids = [item.get("effect_id") for item in collection]
        if any(not LOCAL_ID.fullmatch(str(item)) for item in ids) or len(ids) != len(set(ids)):
            raise ContextError("plan_preview_mismatch", f"{label} effect ids are invalid or duplicate", exit_code=EXIT_CONFLICT)
    effect_ids = {item["effect_id"] for item in effects}
    operation_ids = {item["effect_id"] for item in operations}
    if effect_ids != operation_ids:
        raise ContextError("plan_preview_mismatch", "effects and operations are not 1:1", exit_code=EXIT_CONFLICT)
    draft_ids = {item["effect_id"] for item in drafts}
    for draft in drafts:
        document = parse_document(draft.get("content", ""), descriptor if profile is not None else None)
        if document.warnings:
            warning = document.warnings[0]
            raise ContextError(
                warning["code"],
                "removed legacy fields cannot be introduced by a new artifact draft",
                {key: value for key, value in warning.items() if key != "code"},
                EXIT_CONFLICT,
            )
        projection = draft.get("semantic_projection")
        if not isinstance(projection, dict) or set(projection) != {"kind", "primary_claim", "supporting_context"}:
            raise ContextError("owner_result_invalid", "draft semantic projection is invalid", exit_code=EXIT_CONFLICT)
        draft_kind = (
            kind
            if profile is not None and document.frontmatter.get("schema") == descriptor.get("artifact_schema")
            else {
                "context-snapshot/v1": "snapshot",
                "context-observation/v1": "observation",
                "context-archive/v1": "archive",
                "context-decision/v1": "decision",
            }.get(document.frontmatter.get("schema"))
        )
        if draft_kind != kind and not (
            result["transition"] == "decision_fallback_import"
            and kind == "decision"
            and draft_kind == "observation"
        ):
            raise ContextError("owner_result_invalid", "draft kind escapes the owner transition", exit_code=EXIT_CONFLICT)
        primary_name = (
            profile["sections"]["primary"]
            if profile is not None and draft_kind == kind
            else {
                "snapshot": "Current context",
                "observation": "Observation",
                "archive": "Content",
                "decision": "Decision",
            }.get(draft_kind)
        )
        if (
            primary_name is None
            or projection["kind"] != draft_kind
            or projection["primary_claim"] != _section_value(document.sections, document.frontmatter["schema"], _canonical_section_name(document.frontmatter["schema"], primary_name))
            or not isinstance(projection["supporting_context"], list)
            or len(projection["supporting_context"]) > 4
        ):
            raise ContextError("owner_result_invalid", "draft semantic projection differs from artifact content", exit_code=EXIT_CONFLICT)
    for operation in operations:
        if operation.get("op") not in {"create", "replace", "move", "delete"}:
            raise ContextError("plan_preview_mismatch", "owner operation is not allowed", exit_code=EXIT_CONFLICT)
        if operation["op"] != "delete" and operation["effect_id"] not in draft_ids:
            raise ContextError("plan_preview_mismatch", "non-delete operation lacks a complete destination draft", exit_code=EXIT_CONFLICT)


def _material(material_id: str, path: str | None, content: str) -> dict[str, Any]:
    return {"material_id": material_id, "path": path, "content": content}


def vault_identity(repo: pathlib.Path) -> dict[str, Any]:
    try:
        root = repo.resolve(strict=True)
        metadata = root.stat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("vault root is not a directory")
    except (OSError, RuntimeError) as error:
        raise ContextError("vault_not_found", "vault directory could not be resolved", exit_code=EXIT_NOT_FOUND) from error
    return {
        "schema": "context-vault-identity/v1",
        "root": {"path": str(root), "device": str(metadata.st_dev), "inode": str(metadata.st_ino)},
    }


def _validate_vault_identity(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"schema", "root"}:
        raise ContextError("vault_identity_invalid", "vault identity is missing or obsolete; create a new approval preview", exit_code=EXIT_CONFLICT)
    if value.get("schema") != "context-vault-identity/v1":
        raise ContextError("vault_identity_invalid", "vault identity schema is invalid; create a new approval preview", exit_code=EXIT_CONFLICT)
    entry = value["root"]
    if (
        not isinstance(entry, dict)
        or set(entry) != {"path", "device", "inode"}
        or not isinstance(entry.get("path"), str)
        or not pathlib.Path(entry["path"]).is_absolute()
        or not isinstance(entry.get("device"), str)
        or not entry["device"].isdigit()
        or not isinstance(entry.get("inode"), str)
        or not entry["inode"].isdigit()
    ):
        raise ContextError("vault_identity_invalid", "vault identity entry is invalid", {"field": "root"}, EXIT_CONFLICT)


def _require_vault_identity(repo: pathlib.Path, approval_material: Any) -> dict[str, Any]:
    if not isinstance(approval_material, dict):
        raise ContextError("bundle_invalid", "approval material is invalid", exit_code=EXIT_CONFLICT)
    expected = approval_material.get("vault_identity")
    _validate_vault_identity(expected)
    actual = vault_identity(repo)
    if expected != actual:
        raise ContextError(
            "vault_identity_mismatch",
            "approved mutation belongs to a different vault; create a new approval preview",
            {"expected": expected, "actual": actual},
            EXIT_CONFLICT,
        )
    return actual


def _bundle_result(repo: pathlib.Path, preview: dict[str, Any], plan: dict[str, Any], materials: list[dict[str, Any]]) -> dict[str, Any]:
    effects = preview.get("effects", []) if isinstance(preview, dict) else []
    preview_limit = (
        MAX_ARCHIVE_ENVELOPE_BYTES
        if effects and all(isinstance(effect, dict) and effect.get("area") == "archive" for effect in effects)
        else MAX_APPROVAL_PREVIEW_BYTES
    )
    if len(canonical_json(preview).encode("utf-8")) > preview_limit:
        raise ContextError(
            "approval_preview_too_large",
            "grouped approval preview exceeds its bounded byte budget; split the candidates",
            exit_code=EXIT_CONFLICT,
        )
    approval_material = {"vault_identity": vault_identity(repo), "preview": preview, "plan": plan}
    if plan.get("source_type") == "owner_result":
        approval_material["project_policy"] = _config_call("binding", sys.modules[__name__], repo)
    digest = canonical_digest(approval_material)
    bundle = {"schema": "context-mutation-bundle/v1", "approval_material": approval_material, "approval_digest": digest, "materials": materials}
    return {
        "bundle": bundle,
        "approval_preview": preview,
        "approval_digest": digest,
        "applied": False,
        "state": "awaiting_approval",
        "noop": False,
    }


def _core_identity() -> dict[str, str]:
    entrypoint = pathlib.Path(__file__).resolve(strict=True)
    if not entrypoint.is_file():
        raise ContextError("core_surface_unavailable", "context-core entrypoint is unavailable", exit_code=EXIT_CONFLICT)
    return {"path": str(entrypoint), "sha256": sha256_bytes(entrypoint.read_bytes())}


def _workflow_approval_digest(core: dict[str, str], plan_bundle: dict[str, Any]) -> str:
    return canonical_digest({"core": core, "plan_bundle": plan_bundle})


def _path_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_receipt_location(repo: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    if not path.is_absolute() or ".." in path.parts:
        raise ContextError(
            "receipt_path_invalid",
            "receipt path must be absolute and traversal-free",
            {"path": str(path)},
            EXIT_CONFLICT,
        )
    try:
        resolved = path.resolve(strict=False)
        identity = vault_identity(repo)
        root = pathlib.Path(identity["root"]["path"])
    except (OSError, RuntimeError) as error:
        raise ContextError("receipt_path_invalid", "receipt path could not be resolved", exit_code=EXIT_CONFLICT) from error
    if _path_within(resolved, root):
        raise ContextError(
            "receipt_path_unsafe",
            "receipt path must remain outside the vault",
            {"path": str(path)},
            EXIT_CONFLICT,
        )
    return resolved


def _require_private_receipt_parent(repo: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    resolved = _validate_receipt_location(repo, path)
    parent = path.parent
    try:
        parent_lstat = parent.lstat()
        parent_stat = parent.stat()
    except OSError as error:
        raise ContextError(
            "receipt_path_invalid",
            "receipt parent directory is unavailable",
            {"path": str(parent)},
            EXIT_CONFLICT,
        ) from error
    owner_mismatch = hasattr(os, "getuid") and parent_stat.st_uid != os.getuid()
    if (
        stat.S_ISLNK(parent_lstat.st_mode)
        or not stat.S_ISDIR(parent_stat.st_mode)
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
        or owner_mismatch
    ):
        raise ContextError(
            "receipt_path_unsafe",
            "receipt parent directory must be a private non-symlink directory",
            {"path": str(parent)},
            EXIT_CONFLICT,
        )
    try:
        target_lstat = path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ContextError(
            "receipt_path_invalid",
            "receipt target could not be inspected",
            {"path": str(path)},
            EXIT_CONFLICT,
        ) from error
    else:
        if stat.S_ISLNK(target_lstat.st_mode):
            raise ContextError(
                "receipt_path_unsafe",
                "receipt target must not be a symlink",
                {"path": str(path)},
                EXIT_CONFLICT,
            )
    return resolved


def _open_receipt_parent_fd(path: pathlib.Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path.parent, flags)
    metadata = os.fstat(fd)
    owner_mismatch = hasattr(os, "getuid") and metadata.st_uid != os.getuid()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or owner_mismatch
    ):
        os.close(fd)
        raise OSError("receipt parent is not a private directory")
    return fd


def _default_receipt_path(repo: pathlib.Path, plan_id: str) -> pathlib.Path:
    try:
        temp_root = pathlib.Path(tempfile.gettempdir()).resolve(strict=True)
    except OSError as error:
        raise ContextError("receipt_path_invalid", "default receipt root is unavailable", exit_code=EXIT_CONFLICT) from error
    directory = temp_root / "context-core"
    path = directory / f"{plan_id}.json"
    _validate_receipt_location(repo, path)
    if directory.exists() or directory.is_symlink():
        return _require_private_receipt_parent(repo, path)
    try:
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    except OSError as error:
        raise ContextError(
            "receipt_path_invalid",
            "private receipt directory could not be created",
            {"path": str(directory)},
            EXIT_CONFLICT,
        ) from error
    return _require_private_receipt_parent(repo, path)


def _explicit_receipt_path(repo: pathlib.Path, value: str | pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(value)
    return _require_private_receipt_parent(repo, path)


def _write_frozen_receipt(path: pathlib.Path, receipt: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ContextError(
            "receipt_exists",
            "receipt path already exists",
            {"path": str(path)},
            EXIT_CONFLICT,
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    parent_fd: int | None = None
    try:
        parent_fd = _open_receipt_parent_fd(path)
        fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
        try:
            os.fchmod(fd, 0o600)
            content = canonical_json(receipt).encode("utf-8")
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(fd)
        os.fsync(parent_fd)
    except FileExistsError as error:
        raise ContextError(
            "receipt_exists",
            "receipt path already exists",
            {"path": str(path)},
            EXIT_CONFLICT,
        ) from error
    except OSError as error:
        if parent_fd is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path.name, dir_fd=parent_fd)
        raise ContextError(
            "receipt_write_failed",
            "frozen receipt could not be written",
            {"path": str(path)},
            EXIT_CONFLICT,
        ) from error
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def freeze_bundle_receipt(
    repo: pathlib.Path,
    preview_result: dict[str, Any],
    receipt_file: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    bundle = preview_result.get("bundle")
    if not isinstance(bundle, dict):
        raise ContextError("bundle_invalid", "preview result does not contain a mutation bundle", exit_code=EXIT_CONFLICT)
    nested_digest = bundle.get("approval_digest")
    if not isinstance(nested_digest, str):
        raise ContextError("bundle_invalid", "mutation bundle approval digest is missing", exit_code=EXIT_CONFLICT)
    plan, _ = _validate_bundle(repo, bundle, nested_digest)
    plan_id = plan.get("plan_id")
    if not _valid_plan_id(plan_id):
        raise ContextError("bundle_invalid", "mutation bundle plan id is invalid", exit_code=EXIT_CONFLICT)
    core = _core_identity()
    receipt_body: dict[str, Any] = {
        "schema": CORE_RECEIPT_SCHEMA,
        "status": "pending",
        "created_at": _timestamp(),
        "plan_id": plan_id,
        "core": core,
        "plan_bundle": bundle,
    }
    receipt = {**receipt_body, "receipt_digest": canonical_digest(receipt_body)}
    path = (
        _default_receipt_path(repo, plan_id)
        if receipt_file is None
        else _explicit_receipt_path(repo, receipt_file)
    )
    _write_frozen_receipt(path, receipt)
    return {
        "approval_preview": preview_result["approval_preview"],
        "approval_digest": _workflow_approval_digest(core, bundle),
        "receipt_file": str(path),
        "applied": False,
        "state": "awaiting_approval",
    }


def _maybe_freeze_bundle_receipt(
    repo: pathlib.Path,
    preview_result: dict[str, Any],
    receipt_file: str | pathlib.Path | None,
    *,
    use_default: bool = False,
) -> dict[str, Any]:
    if receipt_file is None and not use_default:
        return preview_result
    return freeze_bundle_receipt(repo, preview_result, receipt_file)


def apply_approved_bundle(
    repo: pathlib.Path,
    preview_result: dict[str, Any],
    receipt_file: str | pathlib.Path | None = None,
    *, approval_source: str = "user", policy_decision: str | None = None, policy_reason: str | None = None,
) -> dict[str, Any]:
    """One-call capture after the caller attests the user's semantic approval.

    Freezes the receipt and applies it unchanged in the same process, exactly like the
    two-phase path; the approval digest, receipt path, and CAS/lock guards stay internal.
    """
    if preview_result.get("noop") is True:
        return preview_result
    frozen = freeze_bundle_receipt(repo, preview_result, receipt_file)
    applied = apply_receipt(repo, frozen["receipt_file"], frozen["approval_digest"],
                            approval_source=approval_source, policy_decision=policy_decision, policy_reason=policy_reason)
    preview = preview_result.get("approval_preview", {})
    paths = {artifact.get("effect_id"): artifact.get("path") for artifact in preview.get("artifacts", [])}
    warnings = list(applied.get("warnings", []))
    return {
        "applied": True,
        "state": "applied",
        "plan_id": applied.get("plan_id"),
        "changed_paths": applied.get("changed_paths", []),
        "artifacts": [
            {"id": effect.get("id"), "path": paths.get(effect.get("effect_id")), "action": effect.get("action")}
            for effect in preview.get("effects", [])
        ],
        "receipt_removed": "receipt_cleanup_failed" not in warnings,
        "warnings": warnings,
        "authorization": applied.get("authorization"),
    }


def _capture_bundle(repo: pathlib.Path, owner_result: dict[str, Any]) -> dict[str, Any]:
    draft = owner_result["artifact_drafts"][0]
    area = owner_result["target_kind"]
    destination = resolve_artifact_path(repo, area, pathlib.PurePosixPath(draft["path"]).name)
    if destination.relative_to(repo).as_posix() != draft["path"]:
        raise ContextError("path_escape", "owner draft path is not canonical", exit_code=EXIT_CONFLICT)
    identifier = owner_result["effects"][0]["id"]
    try:
        _find_artifact(repo, identifier)
    except ContextError as error:
        if error.code != "artifact_not_found":
            raise
    else:
        raise ContextError("duplicate_id", "owner draft id already exists", {"id": identifier}, EXIT_CONFLICT)
    return finalize_owner_result(repo, owner_result)


def build_snapshot_save_bundle(
    repo: pathlib.Path,
    candidate: dict[str, Any],
    attestation: dict[str, Any],
    *,
    filename: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    if candidate.get("requested_kind") != "snapshot":
        raise ContextError("candidate_invalid", "snapshot save requires a snapshot candidate")
    return _capture_bundle(repo, draft_owner_result(candidate, attestation, filename=filename, now=now))


def build_observation_capture_bundle(
    repo: pathlib.Path,
    candidate: dict[str, Any],
    attestation: dict[str, Any],
    *,
    filename: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    if candidate.get("requested_kind") != "observation":
        raise ContextError("candidate_invalid", "observation capture requires an observation candidate")
    return _capture_bundle(repo, draft_owner_result(candidate, attestation, filename=filename, now=now))


def build_archive_capture_bundle(
    repo: pathlib.Path,
    candidate: dict[str, Any],
    attestation: dict[str, Any],
    *,
    filename: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    if candidate.get("requested_kind") != "archive":
        raise ContextError("candidate_invalid", "archive capture requires an archive candidate")
    return _capture_bundle(repo, draft_owner_result(candidate, attestation, filename=filename, now=now))


def _semantic_projection(kind: str, document: Document) -> dict[str, Any]:
    schema = document.frontmatter["schema"]
    primary_name = {"snapshot": "Current context", "observation": "Observation", "archive": "Content"}[kind]
    supporting_name = {"snapshot": "Open items", "observation": "Evidence", "archive": None}[kind]
    if supporting_name is None:
        return {
            "kind": kind,
            "primary_claim": _section_value(document.sections, schema, primary_name),
            "supporting_context": [],
        }
    supporting_body = _section_value(document.sections, schema, supporting_name) or ""
    supporting = [line[2:].strip() for line in supporting_body.splitlines() if line.startswith("- ")][:4]
    return {
        "kind": kind,
        "primary_claim": _section_value(document.sections, schema, primary_name),
        "supporting_context": supporting,
    }


def _mutation_request(
    transition: str,
    kind: str,
    requested_changes: dict[str, Any],
    targets: Sequence[tuple[pathlib.Path, Document]],
    repo: pathlib.Path,
    *,
    successor_owner_result_digest: str | None = None,
    successor_artifact_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "context-domain-mutation-input/v1",
        "transition": transition,
        "owner": "context-core",
        "target_kind": kind,
        "requested_changes": requested_changes,
        "targets": sorted(
            (
                {
                    "id": document.frontmatter["id"],
                    "path": path.relative_to(repo).as_posix(),
                    "sha256": sha256_bytes(path.read_bytes()),
                }
                for path, document in targets
            ),
            key=lambda item: item["path"],
        ),
        "successor_owner_result_digest": successor_owner_result_digest,
        "successor_artifact_sha256": successor_artifact_sha256,
    }


def _mutation_result(
    kind: str,
    transition: str,
    request: dict[str, Any],
    drafts: list[dict[str, Any]],
    effects: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    *,
    extra_inputs: Sequence[dict[str, Any]] = (),
    attestations: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    inputs = list(extra_inputs) + [{"operation": "mutation_request", "input_schema": request["schema"], "input_digest": canonical_digest(request), "value": request}]
    return {
        "schema": "context-owner-result/v1",
        "result_type": "mutation",
        "transition": transition,
        "owner": "context-core",
        "target_kind": kind,
        "capability_digest": canonical_digest(builtin_capability(kind)),
        "semantic_inputs": inputs,
        "semantic_attestations": list(attestations),
        "artifact_drafts": drafts,
        "effects": effects,
        "proposed_plan": {"schema": "context-owner-plan/v1", "transition": transition, "operations": operations},
    }


def _artifact_in_area(
    repo: pathlib.Path,
    identifier: str,
    area: str,
    *,
    current_only: bool = False,
    verify_unique: bool = True,
    warnings: list[str] | None = None,
) -> tuple[pathlib.Path, Document]:
    found_area, path, document = _find_artifact(repo, identifier, warnings=warnings)
    if found_area != area or (current_only and "/retired/" in path.relative_to(repo).as_posix()):
        raise ContextError("artifact_state_invalid", f"artifact is not a current {area}", {"id": identifier}, EXIT_CONFLICT)
    if verify_unique:
        _assert_artifact_id_unique(repo, identifier, path)
    return path, document


def _validate_update_values(
    *,
    title: str | None,
    summary: str | None,
    tags: Sequence[str] | None,
    search_terms: Sequence[str] | None,
    source_refs: Sequence[str] | None,
    anchors: Sequence[str] | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if title is not None:
        if not _substantive(title) or "\n" in title or len(title) > 120:
            raise ContextError("schema_invalid", "title is invalid")
        values["title"] = nfc(title.strip())
    if summary is not None:
        if not _substantive(summary) or "\n" in summary or len(summary) > 280:
            raise ContextError("schema_invalid", "summary is invalid")
        values["summary"] = nfc(summary.strip())
    for field, raw, maximum, item_maximum in (
        ("tags", tags, 12, 40),
        ("search_terms", search_terms, 12, 40),
        ("source_refs", source_refs, 12, 500),
        ("anchors", anchors, 12, 36),
    ):
        if raw is not None:
            values[field] = _string_list(list(raw), field, maximum=maximum, item_maximum=item_maximum)
    for identifier in values.get("anchors", []):
        _require_context_id(identifier, "anchors")
    return values


def build_snapshot_update_bundle(
    repo: pathlib.Path,
    identifier: str,
    *,
    merge: bool = False,
    sections: dict[str, str] | None = None,
    title: str | None = None,
    summary: str | None = None,
    tags: Sequence[str] | None = None,
    search_terms: Sequence[str] | None = None,
    source_refs: Sequence[str] | None = None,
    anchors: Sequence[str] | None = None,
    clear: Sequence[str] = (),
    now: str | None = None,
) -> dict[str, Any]:
    path, document = _artifact_in_area(repo, identifier, "snapshot", current_only=True)
    sections = dict(sections or {})
    sections = _sections_in_existing_style(sections, "context-snapshot/v1", document.sections)
    allowed, required = SECTION_SPECS["context-snapshot/v1"]
    canonical_sections = {_canonical_section_name("context-snapshot/v1", name) for name in sections}
    if canonical_sections - set(allowed):
        raise ContextError("section_schema_error", "snapshot update contains an unknown section")
    if not merge and not set(required).issubset(canonical_sections):
        raise ContextError("snapshot_full_update_required", "full snapshot update requires all required sections", {"required": list(required)})
    for name, content in sections.items():
        if _canonical_section_name("context-snapshot/v1", name) in required and not _substantive(content):
            raise ContextError("section_schema_error", "required snapshot section is not substantive", {"section": name})
    clear_set = set(clear)
    if clear_set - {"anchors", "tags", "search_terms", "source_refs"}:
        raise ContextError("usage_invalid", "snapshot clear target is invalid")
    updates = _validate_update_values(title=title, summary=summary, tags=tags, search_terms=search_terms, source_refs=source_refs, anchors=anchors)
    frontmatter = dict(document.frontmatter)
    for field in clear_set:
        frontmatter.pop(field, None)
    frontmatter.update(updates)
    if merge:
        next_sections = dict(document.sections)
        next_sections.update(sections)
    else:
        next_sections = dict(sections)
        for field in ("anchors", "tags", "search_terms", "source_refs"):
            if field not in updates and field not in clear_set:
                frontmatter.pop(field, None)
    comparison_frontmatter = dict(frontmatter)
    comparison_frontmatter["updated_at"] = document.frontmatter["updated_at"]
    comparison_content = render_document(comparison_frontmatter, next_sections)
    current_content = render_document(document.frontmatter, document.sections)
    if file_bytes(comparison_content) == file_bytes(current_content):
        return {"noop": True, "applied": False, "changed_paths": []}
    frontmatter["updated_at"] = _timestamp(now)
    after_content = render_document(frontmatter, next_sections)
    relative = path.relative_to(repo).as_posix()
    request = _mutation_request(
        "snapshot_update",
        "snapshot",
        {"merge": merge, "sections": sections, "metadata": updates, "clear": sorted(clear_set)},
        [(path, document)],
        repo,
    )
    effect_id = "effect_update_snapshot"
    result = _mutation_result(
        "snapshot",
        "snapshot_update",
        request,
        [{"effect_id": effect_id, "path": relative, "content": after_content, "semantic_projection": _semantic_projection("snapshot", Document(frontmatter, next_sections))}],
        [{"effect_id": effect_id, "action": "replace", "area": "snapshot", "id": identifier, "state": "current"}],
        [{"op": "replace", "effect_id": effect_id, "area": "snapshot", "id": identifier, "path": relative}],
    )
    return finalize_owner_result(repo, result)


def _read_artifact(
    repo: pathlib.Path,
    identifier: str,
    area: str,
    sections: Sequence[str] = (),
    max_bytes: int | None = None,
) -> dict[str, Any]:
    if max_bytes is not None and not 1 <= max_bytes <= MAX_USER_BYTES:
        raise ContextError("usage_invalid", "max-bytes is outside the v1 range")
    warnings: list[str] = []
    path, document = _artifact_in_area(
        repo,
        identifier,
        area,
        verify_unique=False,
        warnings=warnings,
    )
    warnings.extend(warning["code"] for warning in document.warnings)
    schema = document.frontmatter["schema"]
    selected = (
        tuple(_canonical_section_name(schema, name) for name in sections)
        if sections
        else tuple(_canonical_section_name(schema, name) for name in document.sections)
    )
    available = {
        name: value
        for name in selected
        if (value := _section_value(document.sections, schema, name)) is not None
    }
    result: dict[str, Any] = {
        "artifact": dict(document.frontmatter),
        "path": path.relative_to(repo).as_posix(),
        "authority": "staging" if area == "snapshot" else "evidence",
        "warnings": sorted(set(warnings)),
    }
    if area == "snapshot":
        result["use_as"] = "resume_context"
        anchors = document.frontmatter.get("anchors", [])
        if not anchors:
            freshness = "authority_unknown"
        else:
            freshness = "anchored"
            for anchor in anchors:
                try:
                    _, anchor_areas = _root_catalog(repo)
                    _, anchor_row, _ = _indexed_artifact_entry(repo, anchor, anchor_areas)
                    if anchor_row["state"] != "current":
                        freshness = "anchor_changed"
                except (ContextError, OSError, UnicodeError):
                    anchor_warnings: list[str] = []
                    try:
                        _, anchor_path, _ = _find_artifact(repo, anchor, warnings=anchor_warnings)
                        if "/retired/" in anchor_path.relative_to(repo).as_posix():
                            freshness = "anchor_changed"
                    except (ContextError, OSError, UnicodeError):
                        freshness = "anchor_changed"
                    warnings.extend(anchor_warnings)
            if freshness == "anchor_changed":
                warnings.append("anchor_changed")
        result.update({"freshness": freshness, "warnings": sorted(set(warnings))})
    else:
        result["use_as"] = "source_evidence" if area == "archive" else "investigate_or_support"
        result["state"] = "history" if "/retired/" in result["path"] else "current"
    if max_bytes is None:
        return {**result, "sections": available, "truncated": False}
    return _fit_section_payload(
        result,
        available,
        max_bytes,
        complete_fields={"truncated": False},
        truncated_fields={
            "truncated": True,
            "full_read_hint": f"context {area} {'load' if area == 'snapshot' else 'read'} --id {identifier}",
        },
        too_small_code="usage_invalid",
    )


def snapshot_load(
    repo: pathlib.Path,
    identifier: str,
    sections: Sequence[str] = (),
    max_bytes: int | None = None,
) -> dict[str, Any]:
    return _read_artifact(repo, identifier, "snapshot", sections, max_bytes)


def snapshot_list(repo: pathlib.Path, limit: int = 8) -> dict[str, Any]:
    return recall_repository(repo, areas=["snapshot"], limit=limit)


def snapshot_search(repo: pathlib.Path, query: str, limit: int = 8) -> dict[str, Any]:
    return recall_repository(repo, query=query, areas=["snapshot"], limit=limit)


def build_snapshot_discard_bundle(repo: pathlib.Path, identifier: str) -> dict[str, Any]:
    path, _ = _artifact_in_area(repo, identifier, "snapshot", current_only=True)
    del path
    return build_discard_bundle(repo, identifier)


def _core_init_contents() -> dict[str, str]:
    return {
        ROOT_INDEX: render_root_index(_root_seed(), _builtin_area_specs()),
        "context/snapshot/snapshot.index.md": _area_seed(
            "snapshot",
            "context-core",
            "context-snapshot/v1",
            "staging",
            "session handoff staging",
            search_terms=("handoff", "resume"),
        ),
        "context/observation/observation.index.md": _area_seed(
            "observation",
            "context-core",
            "context-observation/v1",
            "evidence",
            "Non-authoritative findings and evidence",
            search_terms=("observation", "evidence"),
        ),
        "context/archive/archive.index.md": _area_seed(
            "archive",
            "context-core",
            "context-archive/v1",
            "evidence",
            "Immutable source material adopted as evidence",
            search_terms=("archive", "source", "evidence"),
        ),
    }


def _core_init_paths(contents: Mapping[str, str] | Mapping[str, Any]) -> list[str]:
    legacy_prefix = [
        ROOT_INDEX,
        "context/observation/observation.index.md",
        "context/snapshot/snapshot.index.md",
    ]
    ordered = [path for path in legacy_prefix if path in contents]
    return ordered + sorted(set(contents) - set(ordered))


def _is_exact_core_init_prefix(repo: pathlib.Path, contents: dict[str, str]) -> bool:
    root_path = repo / "context"
    if not root_path.exists():
        return True
    if root_path.is_symlink() or not root_path.is_dir():
        return False
    entries = list(root_path.rglob("*"))
    if any(path.is_symlink() for path in entries):
        return False
    allowed_directories = {
        "context/snapshot",
        "context/observation",
        "context/observation/retired",
        "context/archive",
        "context/archive/retired",
    }
    present_directories = {
        path.relative_to(repo).as_posix()
        for path in entries
        if path.is_dir()
    }
    if not present_directories.issubset(allowed_directories):
        return False
    present = sorted(
        path.relative_to(repo).as_posix()
        for path in entries
        if path.is_file()
    )
    ordered = _core_init_paths(contents)
    if present != ordered[: len(present)]:
        return False
    return all(
        (repo / relative).read_bytes() == file_bytes(contents[relative])
        for relative in present
    )


def build_init_bundle(repo: pathlib.Path) -> dict[str, Any]:
    root_path = repo / "context"
    paths = [
        repo / ROOT_INDEX,
        repo / "context/snapshot/snapshot.index.md",
        repo / "context/observation/observation.index.md",
        repo / "context/archive/archive.index.md",
    ]
    legacy_paths = paths[:3]
    if all(path.is_file() for path in legacy_paths):
        try:
            _, rows = parse_root_index(paths[0].read_text(encoding="utf-8"))
            archive_rows = [row for row in rows if row["area"] == "archive" or "archive" in row["claims"]]
            if archive_rows != [_builtin_area_specs()[2][0]] or not paths[3].is_file():
                return build_area_register_bundle(
                    repo,
                    {
                        "schema": "context-owner-descriptor/v1",
                        "owner": "context-core",
                        "kind": "archive",
                        "artifact_schema": "context-archive/v1",
                        "authority": "evidence",
                    },
                    _core_init_contents()["context/archive/archive.index.md"],
                )
        except (ContextError, OSError, UnicodeError):
            pass
    existing = [path.is_file() for path in paths]
    if all(existing):
        try:
            _, areas = parse_root_index(paths[0].read_text(encoding="utf-8"))
            rows = {row["area"]: row for row in areas}
            expected = {item[0]["area"]: item[0] for item in _builtin_area_specs()}
            indexes = {
                "snapshot": parse_area_index(paths[1].read_text(encoding="utf-8")),
                "observation": parse_area_index(paths[2].read_text(encoding="utf-8")),
                "archive": parse_area_index(paths[3].read_text(encoding="utf-8")),
            }
            descriptors_match = all(
                rows.get(area) == descriptor
                and {
                    "area": indexes[area].frontmatter["area"],
                    "path": f"context/{area}/{area}.index.md",
                    "owner": indexes[area].frontmatter["owner"],
                    "claims": [area],
                    "artifact_schema": indexes[area].frontmatter["artifact_schema"],
                    "authority": indexes[area].frontmatter["authority"],
                } == descriptor
                for area, descriptor in expected.items()
            )
            if descriptors_match:
                return {"noop": True, "applied": False, "changed_paths": []}
        except (ContextError, OSError, UnicodeError):
            pass
        raise ContextError("partial_core_init", "context root is partially initialized", exit_code=EXIT_CONFLICT)
    contents = _core_init_contents()
    if root_path.exists() and not _is_exact_core_init_prefix(repo, contents):
        raise ContextError("partial_core_init", "context root is partially initialized", exit_code=EXIT_CONFLICT)
    materials = [_material(f"seed_{path.split('/')[-2] if path != ROOT_INDEX else 'root'}", path, content) for path, content in contents.items()]
    material_ids = {material["path"]: material["material_id"] for material in materials}
    before = {path: None for path in contents}
    after = {path: sha256_bytes(file_bytes(content)) for path, content in contents.items()}
    effect_id = "effect_core_init"
    plan = {
        "schema": "context-mutation-plan/v1", "plan_id": new_plan_id(), "owner": "context-core", "source_type": "core_control",
        "transition": "core_init", "owner_descriptor": {"owner": "context-core", "kind": "storage", "artifact_schema": PROTOCOL},
        "control_input": {"schema": "context-core-control/v1", "transition": "core_init", "seed_digests": {path: sha256_bytes(file_bytes(contents[path])) for path in _core_init_paths(contents)}},
        "prior_bundle_digests": [], "read_preconditions": [],
        "operations": [{"op": "index_rebuild", "derived_from": [effect_id], "areas": ["archive", "observation", "snapshot"], "include_root": True, "before_sha256": before, "after_sha256": after, "seed_materials": material_ids}],
    }
    preview = {"schema": "context-approval-preview/v1", "owner": "context-core", "candidate_id": None, "artifacts": [], "effects": [{"effect_id": effect_id, "action": "initialize_core", "paths": _core_init_paths(contents)}]}
    return _bundle_result(repo, preview, plan, materials)


def _bootstrap_phase_error(
    error: ContextError,
    phase: str,
    completed: Sequence[dict[str, Any]],
) -> ContextError:
    details = dict(error.details)
    details["phases"] = [
        *completed,
        {"phase": phase, "status": "failed", "code": error.code, "changed_paths": []},
    ]
    details["retry"] = "Retry the same explicit init call; repair partial or invalid state manually first."
    return ContextError(error.code, error.message, details, error.exit_code)


def _pending_area_resume_bundle(
    repo: pathlib.Path,
    descriptor: dict[str, Any],
    index_seed: str,
) -> dict[str, Any] | None:
    try:
        owner, area, schema, authority = _area_descriptor_fields(descriptor)
        root_path = _ensure_contained(repo, ROOT_INDEX)
        if not root_path.is_file():
            return None
        _, rows = parse_root_index(root_path.read_text(encoding="utf-8"))
        expected = {
            "area": area,
            "path": f"context/{area}/{area}.index.md",
            "owner": owner,
            "claims": [area],
            "artifact_schema": schema,
            "authority": authority,
        }
        if [row for row in rows if row["area"] == area or area in row["claims"]] != [expected]:
            return None
        if _ensure_contained(repo, expected["path"]).exists():
            return None
        result = build_area_register_bundle(repo, descriptor, index_seed)
    except (ContextError, OSError, UnicodeError):
        return None
    return result if result.get("resume_prefix") is True else None


def _policy_target_for_host(host: str | None) -> str:
    target = POLICY_HOST_TARGETS.get(host or "")
    if target is None:
        raise ContextError(
            "host_invalid",
            "explicit init requires host=codex or host=claude-code",
            {"host": host},
            EXIT_CONFLICT,
        )
    return target


def bootstrap_repository(
    repo: pathlib.Path,
    descriptor: dict[str, Any] | None = None,
    index_seed: str | None = None,
    *,
    host: str | None = None,
) -> dict[str, Any]:
    """Apply fixed init seeds and the active host's managed policy.

    An explicit init call authorizes only core_init, area_register, and the
    canonical policy_install block. User-content mutations stay on the ordinary
    exact-digest approval path.
    """

    if (descriptor is None) != (index_seed is None):
        raise ContextError(
            "bootstrap_request_invalid",
            "area bootstrap requires both owner descriptor and index seed",
            exit_code=EXIT_CONFLICT,
        )
    phases: list[dict[str, Any]] = []
    changed_paths: list[str] = []
    repaired_core_paths: list[str] = []
    try:
        policy_target = _policy_target_for_host(host)
        build_policy_bundle(repo, policy_target)
    except ContextError as error:
        raise _bootstrap_phase_error(error, "policy_preflight", phases) from error
    context_root = repo / "context"
    if (
        context_root.is_dir()
        and not (repo / ROOT_INDEX).is_file()
        and any(path.is_file() for path in context_root.glob("*/*.index.md"))
    ):
        try:
            repair = repair_derived_indexes(repo)
            repaired_core_paths = repair["changed_paths"]
            changed_paths.extend(repaired_core_paths)
        except ContextError as error:
            raise _bootstrap_phase_error(error, "core_init", phases) from error
    area_resume = (
        _pending_area_resume_bundle(repo, descriptor, index_seed)
        if descriptor is not None and index_seed is not None
        else None
    )
    if area_resume is not None:
        phases.append({"phase": "core_init", "status": "noop", "changed_paths": []})
    else:
        try:
            core = build_init_bundle(repo)
            if core.get("noop") is True:
                phases.append({
                    "phase": "core_init",
                    "status": "applied" if repaired_core_paths else "noop",
                    "changed_paths": repaired_core_paths,
                })
            else:
                applied = apply_bundle(
                    repo,
                    core["bundle"],
                    core["approval_digest"],
                    approval_source="explicit_init",
                )
                changed_paths.extend(applied["changed_paths"])
                phases.append(
                    {
                        "phase": "core_init",
                        "status": "applied",
                        "changed_paths": sorted(set([*repaired_core_paths, *applied["changed_paths"]])),
                    }
                )
        except ContextError as error:
            raise _bootstrap_phase_error(error, "core_init", phases) from error

    if descriptor is not None and index_seed is not None:
        try:
            area = area_resume or build_area_register_bundle(repo, descriptor, index_seed)
            if area.get("noop") is True:
                phases.append({"phase": "area_register", "status": "noop", "changed_paths": []})
            else:
                applied = apply_bundle(
                    repo,
                    area["bundle"],
                    area["approval_digest"],
                    approval_source="explicit_init",
                )
                changed_paths.extend(applied["changed_paths"])
                phases.append(
                    {"phase": "area_register", "status": "applied", "changed_paths": applied["changed_paths"]}
                )
        except ContextError as error:
            raise _bootstrap_phase_error(error, "area_register", phases) from error

    doctor = doctor_repository(repo)

    try:
        policy_bundle = build_policy_bundle(repo, policy_target)
        if policy_bundle.get("noop") is True:
            policy_status = "noop"
            policy_changed: list[str] = []
        else:
            applied = apply_bundle(
                repo,
                policy_bundle["bundle"],
                policy_bundle["approval_digest"],
                approval_source="explicit_init",
            )
            policy_status = "applied"
            policy_changed = applied["changed_paths"]
            changed_paths.extend(policy_changed)
        phases.append({"phase": "policy_install", "status": policy_status, "changed_paths": policy_changed})
    except ContextError as error:
        raise _bootstrap_phase_error(error, "policy_install", phases) from error

    merge_requested = _git_metadata_root(repo) is not None
    merge_status = "skipped"
    if merge_requested:
        try:
            merge_bundle = build_policy_bundle(repo, MERGE_ATTRIBUTES_TARGET)
            if merge_bundle.get("noop") is True:
                merge_status = "noop"
                merge_changed: list[str] = []
            else:
                applied = apply_bundle(
                    repo,
                    merge_bundle["bundle"],
                    merge_bundle["approval_digest"],
                    approval_source="explicit_init",
                )
                merge_status = "applied"
                merge_changed = applied["changed_paths"]
                changed_paths.extend(merge_changed)
            phases.append({"phase": "merge_attributes_install", "status": merge_status, "changed_paths": merge_changed})
        except ContextError as error:
            raise _bootstrap_phase_error(error, "merge_attributes_install", phases) from error

    return {
        "schema": "context-core-bootstrap-result/v1",
        "applied": any(phase["status"] == "applied" for phase in phases),
        "noop": all(phase["status"] == "noop" for phase in phases),
        "phases": phases,
        "changed_paths": sorted(set(changed_paths)),
        "doctor": doctor,
        "policy": {
            "requested": True,
            "target": policy_target,
            "applied": policy_status == "applied",
            "noop": policy_status == "noop",
        },
        "merge_attributes": {
            "requested": merge_requested,
            "target": MERGE_ATTRIBUTES_TARGET,
            "applied": merge_status == "applied",
            "noop": merge_status == "noop",
        },
    }


def _root_catalog(repo: pathlib.Path) -> tuple[str, list[dict[str, Any]]]:
    path = repo / ROOT_INDEX
    if not path.is_file():
        raise ContextError("context_root_missing", "context root index is missing", {"path": ROOT_INDEX}, EXIT_NOT_FOUND)
    text = path.read_text(encoding="utf-8")
    _, rows = parse_root_index(text)
    return text, rows


def _area_descriptor_fields(descriptor: dict[str, Any]) -> tuple[str, str, str, str]:
    return validate_owner_descriptor(descriptor)


def _profile_registry_entry(descriptor: dict[str, Any]) -> dict[str, Any]:
    if descriptor.get("schema") != "context-owner-descriptor/v2":
        raise ContextError("owner_descriptor_invalid", "only v2 descriptors have a profile registry entry", exit_code=EXIT_CONFLICT)
    return {
        "area": descriptor["kind"],
        "descriptor_schema": descriptor["schema"],
        "descriptor_digest": canonical_digest(descriptor),
    }


def _descriptor_for_area_bytes(
    root_text: str,
    row: dict[str, Any],
    index_text: str,
) -> dict[str, Any] | None:
    profiles = [item for item in parse_root_profiles(root_text) if item["area"] == row["area"]]
    descriptor = parse_area_profile(index_text)
    if not profiles and descriptor is None:
        return None
    if len(profiles) != 1 or descriptor is None:
        raise ContextError(
            "owner_profile_mismatch",
            "root profile registry and area descriptor must both exist for a v2 area",
            {"area": row["area"]},
            EXIT_CONFLICT,
        )
    owner, area, artifact_schema, authority = validate_owner_descriptor(descriptor)
    if (
        profiles[0] != _profile_registry_entry(descriptor)
        or (area, owner, artifact_schema, authority)
        != (row["area"], row["owner"], row["artifact_schema"], row["authority"])
    ):
        raise ContextError(
            "owner_profile_mismatch",
            "root descriptor digest, area profile, and area row differ",
            {"area": row["area"]},
            EXIT_CONFLICT,
        )
    return descriptor


def _best_effort_area_descriptor(
    repo: pathlib.Path,
    row: dict[str, Any],
    root_text: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a read-only descriptor, retaining legacy fallback behavior on drift."""

    relative = row.get("path", f"context/{row['area']}/{row['area']}.index.md")
    try:
        index_text = _ensure_contained(repo, relative).read_text(encoding="utf-8")
    except (ContextError, OSError, UnicodeError):
        return None
    if root_text is not None and all(
        isinstance(row.get(field), str)
        for field in ("area", "path", "owner", "artifact_schema", "authority")
    ):
        try:
            return _descriptor_for_area_bytes(root_text, row, index_text)
        except ContextError:
            pass
    try:
        return parse_area_profile(index_text)
    except ContextError:
        return None


def _registered_area_spec(repo: pathlib.Path, row: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    index_text = _ensure_contained(repo, row["path"]).read_text(encoding="utf-8")
    index = parse_area_index(index_text)
    metadata = index.frontmatter
    if (metadata["area"], metadata["owner"], metadata["artifact_schema"], metadata["authority"]) != (
        row["area"],
        row["owner"],
        row["artifact_schema"],
        row["authority"],
    ):
        raise ContextError(
            "owner_descriptor_conflict",
            "registered area index metadata differs from its root descriptor",
            {"area": row["area"], "path": row["path"]},
            EXIT_CONFLICT,
        )
    root_text = _ensure_contained(repo, ROOT_INDEX).read_text(encoding="utf-8")
    _descriptor_for_area_bytes(root_text, row, index_text)
    return row, _area_label(row["area"]), metadata["summary"]


def build_area_register_bundle(repo: pathlib.Path, descriptor: dict[str, Any], index_seed: str | None) -> dict[str, Any]:
    if index_seed is None:
        raise ContextError("index_seed_required", "area registration requires a complete index seed", exit_code=EXIT_CONFLICT)
    owner, area, schema, authority = _area_descriptor_fields(descriptor)
    seed_index = parse_area_index(index_seed)
    seed_descriptor = parse_area_profile(index_seed)
    is_profiled = descriptor.get("schema") == "context-owner-descriptor/v2"
    if (is_profiled and seed_descriptor != descriptor) or (not is_profiled and seed_descriptor is not None):
        raise ContextError("index_seed_invalid", "area seed owner profile does not match descriptor", exit_code=EXIT_CONFLICT)
    if seed_index.current or seed_index.history:
        raise ContextError("index_seed_invalid", "area seed generated blocks must be empty", exit_code=EXIT_CONFLICT)
    fm = seed_index.frontmatter
    if (fm["area"], fm["owner"], fm["artifact_schema"], fm["authority"]) != (area, owner, schema, authority):
        raise ContextError("index_seed_invalid", "area seed does not match descriptor", exit_code=EXIT_CONFLICT)
    root_text, rows = _root_catalog(repo)
    root_profiles = parse_root_profiles(root_text)
    expected_profile = _profile_registry_entry(descriptor) if is_profiled else None
    area_path = f"context/{area}/{area}.index.md"
    expected_row = {
        "area": area,
        "path": area_path,
        "owner": owner,
        "claims": [area],
        "artifact_schema": schema,
        "authority": authority,
    }
    path = _ensure_contained(repo, area_path)
    area_root = _ensure_contained(repo, f"context/{area}")
    seed_bytes = file_bytes(index_seed)
    seed_digest = sha256_bytes(seed_bytes)
    area_before: str | None = None
    matching = [row for row in rows if row["area"] == area or area in row["claims"]]
    resume_prefix = False
    root_before = root_text
    if matching:
        if matching != [expected_row]:
            raise ContextError(
                "owner_descriptor_conflict",
                "existing area registration differs from the requested descriptor",
                {"area": area},
                EXIT_CONFLICT,
            )
        matching_profiles = [item for item in root_profiles if item["area"] == area]
        if (is_profiled and matching_profiles != [expected_profile]) or (not is_profiled and matching_profiles):
            raise ContextError(
                "owner_descriptor_conflict",
                "existing immutable owner profile differs from the requested descriptor",
                {"area": area},
                EXIT_CONFLICT,
            )
        if path.is_file():
            existing_text = path.read_text(encoding="utf-8")
            existing_index = parse_area_index(existing_text)
            metadata = existing_index.frontmatter
            if (metadata["area"], metadata["owner"], metadata["artifact_schema"], metadata["authority"]) != (
                area,
                owner,
                schema,
                authority,
            ):
                raise ContextError(
                    "owner_descriptor_conflict",
                    "existing area index differs from the requested descriptor",
                    {"area": area, "path": area_path},
                    EXIT_CONFLICT,
                )
            existing_descriptor = _descriptor_for_area_bytes(root_text, expected_row, existing_text)
            if (is_profiled and existing_descriptor != descriptor) or (not is_profiled and existing_descriptor is not None):
                raise ContextError(
                    "owner_descriptor_conflict",
                    "existing immutable area descriptor differs from the requested descriptor",
                    {"area": area},
                    EXIT_CONFLICT,
                )
            return {"noop": True, "applied": False, "changed_paths": []}
        if path.exists():
            raise ContextError(
                "owner_descriptor_conflict",
                "registered area index path is not a regular file",
                {"area": area, "path": area_path},
                EXIT_CONFLICT,
            )
        if area_root.exists() and (not area_root.is_dir() or any(area_root.iterdir())):
            raise ContextError(
                "partial_area_register",
                "registered area has noncanonical content while its index is missing",
                {"area": area, "path": area_path},
                EXIT_CONFLICT,
            )
        prior_specs = [_registered_area_spec(repo, row) for row in rows if row["area"] != area]
        root_before = render_root_index(root_text, prior_specs)
        root_before = render_root_profiles(root_before, [item for item in root_profiles if item["area"] != area])
        expected_after = render_root_index(
            root_before,
            [*prior_specs, (expected_row, _area_label(area), fm["summary"])],
        )
        if expected_profile is not None:
            expected_after = render_root_profiles(expected_after, [
                *[item for item in root_profiles if item["area"] != area],
                expected_profile,
            ])
        if expected_after != root_text:
            raise ContextError(
                "partial_area_register",
                "registered area root row is not the exact canonical write prefix",
                {"area": area, "path": ROOT_INDEX},
                EXIT_CONFLICT,
            )
        root_after = root_text
        resume_prefix = True
    else:
        if path.exists():
            if not path.is_file():
                raise ContextError(
                    "owner_descriptor_conflict",
                    "unregistered area index path is not a regular file",
                    {"area": area, "path": area_path},
                    EXIT_CONFLICT,
                )
            existing_bytes = path.read_bytes()
            if existing_bytes != seed_bytes:
                try:
                    existing_index = parse_area_index(existing_bytes.decode("utf-8"))
                except (ContextError, UnicodeError) as error:
                    raise ContextError(
                        "partial_area_register",
                        "unregistered area index is not the exact approved empty seed",
                        {"area": area, "path": area_path},
                        EXIT_CONFLICT,
                    ) from error
                metadata = existing_index.frontmatter
                if (metadata["area"], metadata["owner"], metadata["artifact_schema"], metadata["authority"]) != (
                    area,
                    owner,
                    schema,
                    authority,
                ):
                    raise ContextError(
                        "owner_descriptor_conflict",
                        "unregistered area index differs from the requested descriptor",
                        {"area": area, "path": area_path},
                        EXIT_CONFLICT,
                    )
                raise ContextError(
                    "partial_area_register",
                    "unregistered area index is populated or differs from the approved empty seed",
                    {"area": area, "path": area_path},
                    EXIT_CONFLICT,
                )
            area_before = seed_digest
            if any(entry.name != path.name for entry in area_root.iterdir()):
                raise ContextError(
                    "partial_area_register",
                    "unregistered area contains content beyond the exact approved empty seed",
                    {"area": area, "path": area_path},
                    EXIT_CONFLICT,
                )
        elif area_root.exists() and (not area_root.is_dir() or any(area_root.iterdir())):
            raise ContextError(
                "partial_area_register",
                "unregistered area has noncanonical content",
                {"area": area, "path": area_path},
                EXIT_CONFLICT,
            )
        specs = [_registered_area_spec(repo, row) for row in rows]
        root_after = render_root_index(
            root_text,
            [*specs, (expected_row, _area_label(area), fm["summary"])],
        )
        if expected_profile is not None:
            root_after = render_root_profiles(root_after, [*root_profiles, expected_profile])
    contents = {ROOT_INDEX: root_after, area_path: index_seed}
    materials = [_material("material_root_index", ROOT_INDEX, root_after), _material("seed_area_index", area_path, index_seed)]
    effect_id = "effect_register_area"
    plan = {
        "schema": "context-mutation-plan/v1", "plan_id": new_plan_id(), "owner": owner, "source_type": "core_control", "transition": "area_register",
        "owner_descriptor": descriptor, "control_input": {"schema": "context-core-control/v1", "transition": "area_register", "descriptor_digest": canonical_digest(descriptor), "seed_digests": {area_path: seed_digest}},
        "prior_bundle_digests": [], "read_preconditions": [],
        "operations": [{"op": "index_rebuild", "derived_from": [effect_id], "areas": [area], "include_root": True, "before_sha256": {ROOT_INDEX: sha256_bytes(file_bytes(root_before)), area_path: area_before}, "after_sha256": {path: sha256_bytes(file_bytes(content)) for path, content in contents.items()}, "seed_materials": {area_path: "seed_area_index"}}],
    }
    preview = {"schema": "context-approval-preview/v1", "owner": owner, "candidate_id": None, "artifacts": [], "effects": [{"effect_id": effect_id, "action": "register_area", "area": area, "path": area_path}]}
    result = _bundle_result(repo, preview, plan, materials)
    if resume_prefix:
        result["resume_prefix"] = True
    return result


def build_policy_bundle(repo: pathlib.Path, target: str) -> dict[str, Any]:
    spec = MANAGED_ROOT_FILES.get(target)
    if spec is None or pathlib.PurePosixPath(target).name != target:
        raise ContextError("policy_target_invalid", "policy target must be exact repository-root AGENTS.md, CLAUDE.md, or .gitattributes", {"target": target}, EXIT_CONFLICT)
    POLICY_BEGIN, POLICY_END, POLICY_BODY = spec["begin"], spec["end"], spec["body"]
    path = _ensure_contained(repo, target)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ContextError("policy_file_unsupported", "policy target must be a regular root file", {"target": target}, EXIT_CONFLICT)
    before_bytes: bytes | None = path.read_bytes() if path.exists() else None
    try:
        before = before_bytes.decode("utf-8") if before_bytes is not None else ""
    except UnicodeDecodeError as error:
        raise ContextError(
            "policy_file_unsupported",
            "policy target must be valid UTF-8",
            {"target": target},
            EXIT_CONFLICT,
        ) from error
    if "\r\n" in before and before.replace("\r\n", "").find("\n") >= 0:
        raise ContextError("policy_file_unsupported", "mixed newlines are not supported", {"target": target}, EXIT_CONFLICT)
    if (
        before.count(POLICY_BEGIN) != before.count(POLICY_END)
        or before.count(POLICY_BEGIN) > 1
        or (
            POLICY_BEGIN in before
            and before.find(POLICY_END) < before.find(POLICY_BEGIN)
        )
    ):
        raise ContextError("policy_marker_invalid", "policy marker must be absent or one balanced pair", {"target": target}, EXIT_CONFLICT)
    newline = "\r\n" if "\r\n" in before else "\n"
    policy_body = POLICY_BODY.replace("\n", newline)
    if POLICY_BEGIN in before:
        start = before.index(POLICY_BEGIN)
        end = before.index(POLICY_END, start) + len(POLICY_END)
        after = before[:start] + policy_body + before[end:]
    elif before:
        separator = "" if before.endswith(newline + newline) else (newline if before.endswith(newline) else newline + newline)
        after = before + separator + policy_body + newline
    else:
        after = policy_body + newline
    if after == before:
        return {"noop": True, "applied": False, "changed_paths": []}
    effect_id = spec["effect_id"]
    material_id = spec["material_id"]
    before_digest = sha256_bytes(before_bytes) if before_bytes is not None else None
    after_digest = sha256_bytes(after.encode("utf-8"))
    operation = {
        "op": "file_replace" if before_bytes is not None else "file_create",
        "effect_id": effect_id,
        "role": "policy",
        "path": target,
        "before_sha256": before_digest,
        "after_sha256": after_digest,
        "material": material_id,
    }
    plan = {
        "schema": "context-mutation-plan/v1", "plan_id": new_plan_id(), "owner": "context-core", "source_type": "core_control",
        "transition": spec["transition"], "owner_descriptor": dict(spec["descriptor"]),
        "control_input": {
            "schema": "context-core-control/v1", "transition": spec["transition"], "target": target,
            "before_sha256": before_digest, "outside_bytes_sha256": sha256_bytes((before[:before.find(POLICY_BEGIN)] + before[before.find(POLICY_END) + len(POLICY_END):]).encode("utf-8")) if POLICY_BEGIN in before else sha256_bytes(before.encode("utf-8")),
        },
        "prior_bundle_digests": [], "read_preconditions": [], "operations": [operation],
    }
    preview = {
        "schema": "context-approval-preview/v1", "owner": "context-core", "candidate_id": None,
        "artifacts": [{"effect_id": effect_id, "path": target, "content": after}],
        "effects": [{"effect_id": effect_id, "action": spec["action"], "path": target}],
    }
    return _bundle_result(repo, preview, plan, [_material(material_id, target, after)])


def _profiled_area_for_owner(
    repo: pathlib.Path,
    area: str,
    owner: str,
) -> tuple[dict[str, Any], AreaIndex, dict[str, Any] | None]:
    root_text, rows = _root_catalog(repo)
    matches = [row for row in rows if row["area"] == area]
    if len(matches) != 1 or matches[0]["owner"] != owner:
        raise ContextError("area_owner_mismatch", "owner is not authorized for target area", {"owner": owner, "area": area}, EXIT_CONFLICT)
    row = matches[0]
    index_text = _ensure_contained(repo, row["path"]).read_text(encoding="utf-8")
    parsed = _tolerant_area_index(repo, area, index_text)
    metadata = parsed.frontmatter
    if (
        metadata["area"],
        metadata["owner"],
        metadata["artifact_schema"],
        metadata["authority"],
    ) != (
        row["area"],
        row["owner"],
        row["artifact_schema"],
        row["authority"],
    ):
        raise ContextError(
            "area_index_mismatch",
            "target area index metadata differs from its authoritative root descriptor",
            {"area": area, "path": row["path"]},
            EXIT_CONFLICT,
        )
    descriptor = _descriptor_for_area_bytes(root_text, row, index_text)
    return row, parsed, descriptor


def _area_for_owner(repo: pathlib.Path, area: str, owner: str) -> tuple[dict[str, Any], AreaIndex]:
    row, parsed, _ = _profiled_area_for_owner(repo, area, owner)
    return row, parsed


def _virtual_area_index(
    index: AreaIndex,
    effects: Sequence[dict[str, Any]],
    drafts: dict[str, dict[str, Any]],
    descriptor: dict[str, Any] | None = None,
) -> str:
    current = {row["id"]: dict(row) for row in index.current}
    history = {row["id"]: dict(row) for row in index.history}
    metadata = index.frontmatter
    for effect in effects:
        identifier = effect.get("id")
        action = effect.get("action")
        if action in {"create", "replace", "rename", "retire", "move"}:
            draft = drafts.get(effect["effect_id"])
            if draft is None:
                raise ContextError("plan_preview_mismatch", "effect lacks destination draft", exit_code=EXIT_CONFLICT)
            resolved_descriptor = descriptor if descriptor is not None else parse_area_profile(index.text)
            document = parse_document(draft["content"], resolved_descriptor)
            path = pathlib.Path("/") / draft["path"]
            fake_repo = pathlib.Path("/")
            row = {
                "id": document.frontmatter["id"], "path": draft["path"], "title": document.frontmatter["title"],
                "summary": document.frontmatter["summary"], "state": "history" if "/retired/" in draft["path"] else "current",
                "created_at": document.frontmatter["created_at"],
            }
            if "updated_at" in document.frontmatter:
                row["updated_at"] = document.frontmatter["updated_at"]
            row["terms"] = _terms(document.frontmatter)
            del path, fake_repo
            if row["state"] == "history":
                row["retired_at"] = document.frontmatter["retired_at"]
                row["retired_reason"] = document.frontmatter["retired_reason"]
                if "superseded_by" in document.frontmatter:
                    row["superseded_by"] = document.frontmatter["superseded_by"]
            for key in metadata.get("projection_fields", []):
                if key in document.frontmatter:
                    row[key] = document.frontmatter[key]
            if row["state"] == "history":
                current.pop(identifier, None)
                history[identifier] = row
            else:
                history.pop(identifier, None)
                current[identifier] = row
        elif action == "delete":
            current.pop(identifier, None)
            history.pop(identifier, None)
        else:
            raise ContextError("plan_preview_mismatch", "effect action is unsupported", {"action": action}, EXIT_CONFLICT)
    current_rows = sorted(current.values(), key=lambda row: (row["created_at"], row["id"]))
    history_rows = sorted(history.values(), key=lambda row: (row["created_at"], row["id"]))
    text = _replace_block(index.text, "current", [_entry_row(row) for row in current_rows])
    if metadata["area"] != "snapshot":
        text = _replace_block(text, "history", [_entry_row(row) for row in history_rows])
    return text


def _bundle_owner_result(repo: pathlib.Path, bundle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if bundle.get("schema") != "context-mutation-bundle/v1" or bundle.get("approval_digest") != canonical_digest(bundle.get("approval_material")):
        raise ContextError("prior_bundle_invalid", "prior bundle digest is invalid", exit_code=EXIT_CONFLICT)
    try:
        _require_vault_identity(repo, bundle.get("approval_material"))
    except ContextError as error:
        raise ContextError("prior_bundle_invalid", "prior bundle vault identity is invalid", {"cause": error.code}, EXIT_CONFLICT) from error
    plan = bundle["approval_material"].get("plan", {})
    material = next((item for item in bundle.get("materials", []) if item.get("material_id") == plan.get("owner_result_material")), None)
    if plan.get("source_type") != "owner_result" or material is None or material.get("path") is not None:
        raise ContextError("prior_bundle_invalid", "prior bundle lacks owner result material", exit_code=EXIT_CONFLICT)
    try:
        result = json.loads(material["content"])
    except json.JSONDecodeError as error:
        raise ContextError("prior_bundle_invalid", "prior owner result is not JSON", exit_code=EXIT_CONFLICT) from error
    if canonical_json(result) != material["content"] or sha256_bytes(material["content"].encode("utf-8")) != plan.get("owner_result_digest"):
        raise ContextError("prior_bundle_invalid", "prior owner result material changed", exit_code=EXIT_CONFLICT)
    descriptor = plan.get("owner_descriptor") if plan.get("owner_descriptor", {}).get("schema") == "context-owner-descriptor/v2" else None
    validation = plan.get("owner_validation")
    capability = validation.get("capability") if descriptor is not None and isinstance(validation, dict) else None
    validate_owner_result(result, capability, descriptor)
    return plan, result


def _owner_result_topology(owner_result: dict[str, Any]) -> str:
    proposed_plan = owner_result.get("proposed_plan")
    operations = proposed_plan.get("operations") if isinstance(proposed_plan, dict) else None
    effect_list = owner_result.get("effects")
    draft_list = owner_result.get("artifact_drafts")
    if (
        not isinstance(operations, list)
        or not isinstance(effect_list, list)
        or not isinstance(draft_list, list)
        or any(not isinstance(item, dict) for item in [*operations, *effect_list, *draft_list])
    ):
        raise ContextError("transition_topology_invalid", "owner topology collections are malformed", exit_code=EXIT_CONFLICT)
    effects = {effect.get("effect_id"): effect for effect in effect_list}
    drafts = {draft.get("effect_id"): draft for draft in draft_list}
    if len(operations) == 1:
        operation = operations[0]
        effect = effects.get(operation.get("effect_id"), {})
        op = operation.get("op")
        path = operation.get("path")
        if op == "create" and effect.get("action") == "create" and isinstance(path, str) and "/retired/" not in path:
            return "create_current"
        if op in {"replace", "move"} and effect.get("action") in {"replace", "move", "rename"}:
            before = operation.get("from_path", operation.get("path", ""))
            after = operation.get("to_path", drafts.get(operation.get("effect_id"), {}).get("path", ""))
            if isinstance(before, str) and isinstance(after, str) and ("/retired/" in before) == ("/retired/" in after):
                return "replace_same_state"
        if op == "move" and effect.get("action") == "retire":
            source = operation.get("from_path")
            destination = operation.get("to_path")
            if isinstance(source, str) and isinstance(destination, str) and "/retired/" not in source and "/retired/" in destination:
                return "retire_current"
        if op == "delete" and effect.get("action") == "delete":
            return "delete_one"
    if len(operations) == 2:
        moves = [operation for operation in operations if operation.get("op") == "move"]
        creates = [operation for operation in operations if operation.get("op") == "create"]
        if len(moves) == len(creates) == 1:
            move_effect = effects.get(moves[0].get("effect_id"), {})
            create_effect = effects.get(creates[0].get("effect_id"), {})
            source = moves[0].get("from_path")
            destination = moves[0].get("to_path")
            create_path = creates[0].get("path")
            if (
                move_effect.get("action") == "retire"
                and create_effect.get("action") == "create"
                and isinstance(source, str)
                and isinstance(destination, str)
                and isinstance(create_path, str)
                and "/retired/" not in source
                and "/retired/" in destination
                and "/retired/" not in create_path
            ):
                return "supersede_current"
    raise ContextError("transition_topology_invalid", "owner operations do not match a generic structural topology", exit_code=EXIT_CONFLICT)


def _profile_reference_ids(frontmatter: dict[str, Any], profile: dict[str, Any]) -> set[str]:
    identifiers: set[str] = set()
    for name, spec in profile["fields"].items():
        if name not in frontmatter:
            continue
        value = frontmatter[name]
        if spec["type"] == "context_id":
            identifiers.add(value)
        elif spec["type"] == "context_id_list":
            identifiers.update(value)
        elif spec["type"] == "relation_map":
            for values in value.values():
                identifiers.update(values)
    return identifiers


def _typed_relation_entries(frontmatter: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (relation key, target kind, target id) for typed relations.

    Relation storage stays the existing ``relations: {key: [ctx_id...]}`` shape.
    Keys without ``:`` remain legacy untyped relations. A key containing ``:`` is
    therefore unambiguously opting into the typed-relations/v1 contract.
    """

    relations = frontmatter.get("relations")
    if relations is None:
        return []
    if not isinstance(relations, dict):
        raise ContextError("schema_invalid", "relations must be an object")
    entries: list[tuple[str, str, str]] = []
    for key, identifiers in relations.items():
        if not isinstance(key, str):
            raise ContextError("schema_invalid", "relation key must be a string")
        if ":" not in key:
            continue
        match = TYPED_RELATION_KEY.fullmatch(key)
        if match is None:
            raise ContextError(
                "typed_relation_key_invalid",
                "typed relation key must be <predicate>:<target-kind>",
                {"relation": key},
                EXIT_CONFLICT,
            )
        if (
            not isinstance(identifiers, list)
            or any(not isinstance(identifier, str) for identifier in identifiers)
            or len(identifiers) != len(set(identifiers))
        ):
            raise ContextError(
                "schema_invalid",
                "typed relation targets must be a unique context id list",
                {"relation": key},
            )
        for identifier in identifiers:
            _require_context_id(identifier, "relations")
            entries.append((key, match.group("target_kind"), identifier))
    return entries


def _validate_typed_relations(
    repo: pathlib.Path,
    owner_result: dict[str, Any],
    descriptor: dict[str, Any] | None,
) -> None:
    """Validate typed relation targets against planned drafts and the vault.

    This runs during preview and again during apply validation. It deliberately
    does not add inverse edges or a graph index.
    """

    effects = {effect.get("effect_id"): effect for effect in owner_result.get("effects", [])}
    planned_kinds: dict[str, str] = {}
    documents: list[tuple[str, Document]] = []
    for draft in owner_result.get("artifact_drafts", []):
        effect = effects.get(draft.get("effect_id"), {})
        kind = effect.get("area")
        if not isinstance(kind, str):
            continue
        draft_descriptor = descriptor if descriptor is not None and descriptor.get("kind") == kind else None
        document = parse_document(draft.get("content", ""), draft_descriptor)
        planned_kinds[document.frontmatter["id"]] = kind
        documents.append((draft.get("path", ""), document))

    resolved: dict[str, str] = {}
    for path, document in documents:
        for relation, target_kind, identifier in _typed_relation_entries(document.frontmatter):
            actual_kind = planned_kinds.get(identifier)
            if actual_kind is None:
                if identifier not in resolved:
                    try:
                        resolved[identifier] = _find_artifact(repo, identifier)[0]
                    except ContextError as error:
                        if error.code == "artifact_not_found":
                            raise ContextError(
                                "typed_relation_target_missing",
                                "typed relation target does not exist",
                                {"path": path, "relation": relation, "target": identifier},
                                EXIT_CONFLICT,
                            ) from error
                        raise
                actual_kind = resolved[identifier]
            if actual_kind != target_kind:
                raise ContextError(
                    "typed_relation_kind_mismatch",
                    "typed relation target kind differs from the relation key",
                    {
                        "path": path,
                        "relation": relation,
                        "target": identifier,
                        "expected_kind": target_kind,
                        "actual_kind": actual_kind,
                    },
                    EXIT_CONFLICT,
                )


def _validate_profiled_owner_structure(
    repo: pathlib.Path,
    owner_result: dict[str, Any],
    descriptor: dict[str, Any],
    transition_topology: str,
) -> None:
    profile = descriptor["structural_profile"]
    if transition_topology not in profile["lifecycle"]["allowed_topologies"] or _owner_result_topology(owner_result) != transition_topology:
        raise ContextError("transition_topology_invalid", "owner result topology differs from its structural profile", exit_code=EXIT_CONFLICT)
    effects = {effect["effect_id"]: effect for effect in owner_result["effects"]}
    documents: dict[str, Document] = {}
    states: dict[str, str] = {}
    for draft in owner_result["artifact_drafts"]:
        effect = effects.get(draft["effect_id"])
        if effect is None or effect.get("area") != descriptor["kind"]:
            raise ContextError("area_owner_mismatch", "profiled draft escapes its descriptor area", exit_code=EXIT_CONFLICT)
        document = parse_document(draft["content"], descriptor)
        if document.frontmatter["id"] != effect.get("id"):
            raise ContextError("plan_preview_mismatch", "profiled draft id differs from its effect", exit_code=EXIT_CONFLICT)
        state = "history" if "/retired/" in draft["path"] else "current"
        if effect.get("state") != state:
            raise ContextError("plan_preview_mismatch", "profiled effect state differs from its target path", exit_code=EXIT_CONFLICT)
        _validate_strict_lifecycle(document.frontmatter, state, draft["path"], descriptor)
        documents[draft["effect_id"]] = document
        states[draft["effect_id"]] = state

    proposed_operations = owner_result["proposed_plan"]["operations"]
    for operation in proposed_operations:
        effect = effects[operation["effect_id"]]
        if operation.get("area") != descriptor["kind"] or effect.get("area") != descriptor["kind"]:
            raise ContextError("area_owner_mismatch", "profiled operation escapes its descriptor area", exit_code=EXIT_CONFLICT)
        source = operation.get("from_path", operation.get("path"))
        if operation["op"] in {"replace", "move", "delete"} and isinstance(source, str):
            source_path = _ensure_contained(repo, source)
            if source_path.is_file():
                source_document = parse_document(source_path.read_text(encoding="utf-8"), descriptor)
                if source_document.frontmatter["id"] != effect.get("id"):
                    raise ContextError("plan_preview_mismatch", "profiled source id differs from its effect", exit_code=EXIT_CONFLICT)
                source_state = "history" if "/retired/" in source else "current"
                _validate_strict_lifecycle(source_document.frontmatter, source_state, source, descriptor)

    retired = [effect_id for effect_id, state in states.items() if state == "history"]
    current = [effect_id for effect_id, state in states.items() if state == "current"]
    if transition_topology in {"retire_current", "supersede_current"}:
        if len(retired) != 1 or (transition_topology == "supersede_current" and len(current) != 1):
            raise ContextError("transition_topology_invalid", "profiled lifecycle drafts do not match topology", exit_code=EXIT_CONFLICT)
        predecessor_document = documents[retired[0]]
        reason = predecessor_document.frontmatter["retired_reason"]
        recipe = profile["lifecycle"]["reasons"].get(reason)
        if recipe is None or recipe["topology"] != transition_topology:
            raise ContextError("lifecycle_invalid", "profiled lifecycle reason differs from operation topology", exit_code=EXIT_CONFLICT)
        successor_document = documents[current[0]] if transition_topology == "supersede_current" else None
        predecessor_id = predecessor_document.frontmatter["id"]
        successor_id = successor_document.frontmatter["id"] if successor_document is not None else None
        locations = {"predecessor": predecessor_document, "successor": successor_document}
        targets = {"predecessor": predecessor_id, "successor": successor_id}
        for reference in recipe["references"]:
            document = locations[reference["location"]]
            target = targets[reference["target"]]
            if document is None or target is None:
                raise ContextError("reference_invalid", "profiled lifecycle reference lacks its topology endpoint", exit_code=EXIT_CONFLICT)
            value = document.frontmatter.get(reference["field"])
            matches = value == target if reference["match"] == "equals" else isinstance(value, list) and target in value
            if not matches:
                raise ContextError("reference_invalid", "profiled lifecycle reference does not bind its endpoint", {"field": reference["field"]}, EXIT_CONFLICT)

    referenced_ids = set()
    for document in documents.values():
        referenced_ids.update(_profile_reference_ids(document.frontmatter, profile))
    effect_ids = {effect.get("id") for effect in effects.values() if is_context_id(effect.get("id"))}
    external = referenced_ids - effect_ids
    if external:
        paths = _artifact_id_paths(repo, external)
        missing = sorted(identifier for identifier, values in paths.items() if len(values) != 1)
        if missing:
            raise ContextError("reference_invalid", "profiled artifact reference is missing or ambiguous", {"ids": missing}, EXIT_CONFLICT)
    if transition_topology == "delete_one":
        operation = owner_result["proposed_plan"]["operations"][0]
        target = _ensure_contained(repo, operation["path"])
        inbound = _inbound_refs(repo, effects[operation["effect_id"]]["id"], target)
        if inbound:
            raise ContextError("inbound_reference", "profiled delete target has inbound references", {"paths": inbound}, EXIT_CONFLICT)


def _validate_owner_validation(
    owner_result: dict[str, Any],
    validation: dict[str, Any] | None,
    area_index: AreaIndex,
    same_area_prior_digests: Sequence[str],
    descriptor: dict[str, Any] | None = None,
    *,
    base_area_index_sha256: str | None = None,
    verify_base_area_index: bool = True,
) -> tuple[dict[str, Any] | None, str | None]:
    requires = owner_result["owner"] != "context-core"
    if not requires and validation is None:
        return None, None
    if not isinstance(validation, dict):
        raise ContextError("owner_validation_required", "addon owner result requires a batch validation receipt", exit_code=EXIT_CONFLICT)
    profile = _descriptor_profile(descriptor)
    if profile is not None:
        required_fields = {
            "schema", "owner", "kind", "descriptor_digest", "capability", "owner_result_digest",
            "base_area_index_sha256", "prior_same_area_bundle_digests", "transition_topology",
            "semantic_input_digests", "status", "receipt_digest",
        }
        if set(validation) != required_fields or validation.get("schema") != "context-owner-validation-receipt/v2":
            raise ContextError("owner_validation_invalid", "profiled owner receipt fields are stale or malformed", exit_code=EXIT_CONFLICT)
        capability = validation.get("capability")
        try:
            capabilities = _capability_list([capability])
        except ContextError as error:
            raise ContextError("owner_validation_invalid", "profiled owner receipt capability is invalid", exit_code=EXIT_CONFLICT) from error
        if len(capabilities) != 1:
            raise ContextError("owner_validation_invalid", "profiled owner receipt capability is missing", exit_code=EXIT_CONFLICT)
        capability = capabilities[0]
        transition_topology = validation.get("transition_topology")
        semantic_inputs = owner_result.get("semantic_inputs")
        assertions = capability.get("claim_assertions")
        if (
            not isinstance(semantic_inputs, list)
            or any(not isinstance(item, dict) for item in semantic_inputs)
            or not isinstance(assertions, list)
            or not assertions
            or len(assertions) > MAX_PROFILE_ITEMS
            or len(assertions) != len(set(assertions))
            or any(not isinstance(item, str) or not FIELD_KEY.fullmatch(item) for item in assertions)
        ):
            raise ContextError("owner_validation_invalid", "profiled capability or semantic inputs are malformed", exit_code=EXIT_CONFLICT)
        semantic_digests = {item.get("operation"): item.get("input_digest") for item in semantic_inputs}
        expected = dict(validation)
        receipt_digest = expected.pop("receipt_digest", None)
        descriptor_digest = canonical_digest(descriptor)
        expected_base_digest = base_area_index_sha256 or _index_base_digest(area_index)
        if (
            validation.get("owner") != owner_result["owner"]
            or validation.get("kind") != owner_result["target_kind"]
            or validation.get("descriptor_digest") != descriptor_digest
            or capability.get("owner") != descriptor["owner"]
            or capability.get("kind") != descriptor["kind"]
            or capability.get("artifact_schema") != descriptor["artifact_schema"]
            or capability.get("authority") != descriptor["authority"]
            or capability.get("descriptor_digest") != descriptor_digest
            or canonical_digest(capability) != owner_result.get("capability_digest")
            or validation.get("owner_result_digest") != canonical_digest(owner_result)
            or (verify_base_area_index and validation.get("base_area_index_sha256") != expected_base_digest)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(validation.get("base_area_index_sha256")))
            or validation.get("prior_same_area_bundle_digests") != list(same_area_prior_digests)
            or transition_topology not in profile["lifecycle"]["allowed_topologies"]
            or transition_topology != _owner_result_topology(owner_result)
            or validation.get("semantic_input_digests") != semantic_digests
            or validation.get("status") != "valid"
            or receipt_digest != canonical_digest(expected)
        ):
            raise ContextError("owner_validation_invalid", "profiled owner receipt bindings are stale or malformed", exit_code=EXIT_CONFLICT)
        return capability, transition_topology
    expected = dict(validation)
    receipt_digest = expected.pop("receipt_digest", None)
    if (
        validation.get("schema") != "context-owner-validation-receipt/v1"
        or validation.get("owner") != owner_result["owner"]
        or validation.get("kind") != owner_result["target_kind"]
        or validation.get("owner_result_digest") != canonical_digest(owner_result)
        or validation.get("base_area_index_sha256") != _index_base_digest(area_index)
        or validation.get("prior_same_area_bundle_digests") != list(same_area_prior_digests)
        or validation.get("status") != "valid"
        or receipt_digest != canonical_digest(expected)
    ):
        raise ContextError("owner_validation_invalid", "owner validation receipt is stale or malformed", exit_code=EXIT_CONFLICT)
    return None, None


def finalize_owner_result(repo: pathlib.Path, owner_result: dict[str, Any], owner_validation: dict[str, Any] | None = None, prior_bundles: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    if not isinstance(owner_result, dict) or not isinstance(owner_result.get("owner"), str) or not isinstance(owner_result.get("target_kind"), str):
        raise ContextError("owner_result_invalid", "owner result envelope is invalid", exit_code=EXIT_CONFLICT)
    owner = owner_result["owner"]
    primary_area = owner_result["target_kind"]
    _config_call("require_enabled", repo, [primary_area])
    area_row, physical_area_index, descriptor = _profiled_area_for_owner(repo, primary_area, owner)
    prior_digests: list[str] = []
    same_area_prior_digests: list[str] = []
    virtual_text = physical_area_index.text
    for prior in prior_bundles:
        prior_plan, prior_result = _bundle_owner_result(repo, prior)
        if prior_plan.get("prior_bundle_digests") != prior_digests:
            raise ContextError("prior_bundle_order_invalid", "prior bundle chain differs from exact proposal order", exit_code=EXIT_CONFLICT)
        prior_digest = prior["approval_digest"]
        prior_digests.append(prior_digest)
        if primary_area in {effect.get("area") for effect in prior_result.get("effects", [])}:
            prior_effects = [effect for effect in prior_result["effects"] if effect.get("area") == primary_area]
            prior_drafts = {draft["effect_id"]: draft for draft in prior_result["artifact_drafts"] if any(effect["effect_id"] == draft["effect_id"] for effect in prior_effects)}
            virtual_index = AreaIndex(physical_area_index.frontmatter, parse_area_index(virtual_text).current, parse_area_index(virtual_text).history, virtual_text)
            virtual_text = _virtual_area_index(virtual_index, prior_effects, prior_drafts, descriptor)
            same_area_prior_digests.append(prior_digest)
    capability, transition_topology = _validate_owner_validation(
        owner_result,
        owner_validation,
        physical_area_index,
        same_area_prior_digests,
        descriptor,
    )
    validate_owner_result(owner_result, capability, descriptor)
    _validate_typed_relations(repo, owner_result, descriptor)
    if descriptor is not None:
        assert transition_topology is not None
        _validate_profiled_owner_structure(repo, owner_result, descriptor, transition_topology)
    read_preconditions = owner_result["proposed_plan"].get("read_preconditions", [])
    if len({item.get("path") for item in read_preconditions}) != len(read_preconditions):
        raise ContextError("read_precondition_invalid", "owner read preconditions contain duplicate paths", exit_code=EXIT_CONFLICT)
    for precondition in read_preconditions:
        if set(precondition) != {"id", "path", "sha256"}:
            raise ContextError("read_precondition_invalid", "owner read precondition shape is invalid", exit_code=EXIT_CONFLICT)
        target = _ensure_contained(repo, precondition["path"])
        if not target.is_file() or sha256_bytes(target.read_bytes()) != precondition["sha256"]:
            raise ContextError("precondition_changed", "owner read precondition is stale", {"path": precondition["path"]}, EXIT_CONFLICT)
    area_indexes: dict[str, AreaIndex] = {primary_area: AreaIndex(physical_area_index.frontmatter, parse_area_index(virtual_text).current, parse_area_index(virtual_text).history, virtual_text, physical_area_index.physical_text)}
    area_descriptors: dict[str, dict[str, Any] | None] = {primary_area: descriptor}
    for area in sorted({effect.get("area") for effect in owner_result["effects"] if isinstance(effect.get("area"), str)} - {primary_area}):
        if owner_result["transition"] != "decision_fallback_import" or area != "observation" or owner != "context-decision":
            raise ContextError("area_owner_mismatch", "cross-owner area is not allowlisted", {"area": area}, EXIT_CONFLICT)
        _, area_indexes[area], area_descriptors[area] = _profiled_area_for_owner(repo, area, "context-core")
    drafts = {draft["effect_id"]: draft for draft in owner_result["artifact_drafts"]}
    effects = {effect["effect_id"]: effect for effect in owner_result["effects"]}
    operations: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = []
    owner_material_id = "material_owner_result"
    owner_content = canonical_json(owner_result)
    owner_digest = sha256_bytes(owner_content.encode("utf-8"))
    materials.append(_material(owner_material_id, None, owner_content))
    vacated_paths = {
        operation["from_path"]
        for operation in owner_result["proposed_plan"]["operations"]
        if operation.get("op") == "move"
    }
    for proposed in owner_result["proposed_plan"]["operations"]:
        effect_id = proposed["effect_id"]
        effect = effects[effect_id]
        area = effect.get("area")
        if area not in area_indexes:
            raise ContextError("area_owner_mismatch", "owner plan touches an unauthorized area", exit_code=EXIT_CONFLICT)
        authorized_owner = owner if area == primary_area else "context-core"
        area_record, _ = _area_for_owner(repo, area, authorized_owner)
        operation = proposed["op"]
        draft = drafts.get(effect_id)
        if operation != "delete":
            assert draft is not None
            relative = draft["path"]
            if not relative.startswith(f"context/{area}/") or relative in RESERVED_INDEX_PATHS:
                raise ContextError("path_escape", "draft path is outside the owner area", {"path": relative}, EXIT_CONFLICT)
            _ensure_contained(repo, relative)
            document = parse_document(draft["content"], area_descriptors[area])
            if document.frontmatter["schema"] != area_record["artifact_schema"] or document.frontmatter["id"] != effect.get("id"):
                raise ContextError("plan_preview_mismatch", "draft schema/id does not match effect", exit_code=EXIT_CONFLICT)
            material_id = f"material_{effect_id}"
            materials.append(_material(material_id, relative, draft["content"]))
            after = sha256_bytes(file_bytes(draft["content"]))
        if operation == "create":
            path = proposed["path"]
            if path != draft["path"]:
                raise ContextError("plan_preview_mismatch", "create path and draft differ", exit_code=EXIT_CONFLICT)
            current = repo / path
            if current.exists() and path not in vacated_paths:
                raise ContextError("path_exists", "create target already exists", {"path": path}, EXIT_CONFLICT)
            operations.append({"op": "file_create", "effect_id": effect_id, "role": "artifact", "area": area, "path": path, "before_sha256": None, "after_sha256": after, "material": material_id})
        elif operation == "replace":
            path = proposed["path"]
            target = repo / path
            if not target.is_file():
                raise ContextError("artifact_not_found", "replace target is missing", {"path": path}, EXIT_NOT_FOUND)
            operations.append({"op": "file_replace", "effect_id": effect_id, "role": "artifact", "area": area, "id": effect["id"], "path": path, "before_sha256": sha256_bytes(target.read_bytes()), "after_sha256": after, "material": material_id})
        elif operation == "move":
            source = proposed["from_path"]
            destination = proposed["to_path"]
            if destination != draft["path"]:
                raise ContextError("plan_preview_mismatch", "move destination and draft differ", exit_code=EXIT_CONFLICT)
            source_path = repo / source
            if not source_path.is_file() or (repo / destination).exists():
                raise ContextError("precondition_changed", "move start state is unavailable", exit_code=EXIT_CONFLICT)
            before = sha256_bytes(source_path.read_bytes())
            move: dict[str, Any] = {"op": "file_move", "effect_id": effect_id, "role": "artifact", "area": area, "id": effect["id"], "from_path": source, "to_path": destination, "before_sha256": before, "destination_before_sha256": None, "after_sha256": after}
            if after != before:
                move["material"] = material_id
            else:
                materials = [item for item in materials if item["material_id"] != material_id]
            operations.append(move)
        elif operation == "delete":
            path = proposed["path"]
            target = repo / path
            if not target.is_file():
                raise ContextError("artifact_not_found", "delete target is missing", {"path": path}, EXIT_NOT_FOUND)
            operations.append({"op": "file_delete", "effect_id": effect_id, "role": "artifact", "area": area, "id": effect["id"], "path": path, "before_sha256": sha256_bytes(target.read_bytes()), "inbound_refs": []})
        else:
            raise ContextError("plan_preview_mismatch", "unsupported owner operation", exit_code=EXIT_CONFLICT)
    touched_areas = sorted(area_indexes)
    index_before: dict[str, str] = {}
    index_after: dict[str, str] = {}
    for area in touched_areas:
        matching_effects = [effect for effect in effects.values() if effect.get("area") == area]
        matching_drafts = {key: value for key, value in drafts.items() if any(effect["effect_id"] == key for effect in matching_effects)}
        index = area_indexes[area]
        path = f"context/{area}/{area}.index.md"
        rendered_index = _virtual_area_index(index, matching_effects, matching_drafts, area_descriptors[area])
        index_before[path] = _index_base_digest(index)
        index_after[path] = sha256_bytes(file_bytes(rendered_index))
        index_material_id = f"material_index_{hashlib.sha256(area.encode('utf-8')).hexdigest()[:12]}"
        materials.append(_material(index_material_id, path, rendered_index))
    operations.append({"op": "index_rebuild", "derived_from": sorted(effects), "areas": touched_areas, "include_root": False, "before_sha256": index_before, "after_sha256": index_after})
    plan = {
        "schema": "context-mutation-plan/v1", "plan_id": new_plan_id(), "owner": owner, "source_type": "owner_result",
        "owner_result_digest": owner_digest, "owner_result_material": owner_material_id, "capability_digest": owner_result["capability_digest"],
        "transition": owner_result["transition"], "owner_descriptor": {"owner": owner, "kind": primary_area, "artifact_schema": area_row["artifact_schema"], "authority": area_row["authority"]},
        "owner_validation": owner_validation, "prior_bundle_digests": prior_digests,
        "read_preconditions": owner_result["proposed_plan"].get("read_preconditions", []), "operations": operations,
    }
    if descriptor is not None:
        plan["owner_descriptor"] = descriptor
        plan["descriptor_digest"] = canonical_digest(descriptor)
        plan["transition_topology"] = transition_topology
        plan["prior_same_area_bundle_digests"] = same_area_prior_digests
    preview = {"schema": "context-approval-preview/v1", "owner": owner, "candidate_id": owner_result.get("candidate_id"), "artifacts": [{"effect_id": draft["effect_id"], "path": draft["path"], "content": draft["content"]} for draft in owner_result["artifact_drafts"]], "effects": owner_result["effects"]}
    return _bundle_result(repo, preview, plan, materials)


def _filesystem_lookup_areas(repo: pathlib.Path) -> list[dict[str, Any]]:
    root = _ensure_contained(repo, "context")
    if root.is_symlink():
        raise ContextError("symlink_path", "context root cannot be a symlink", {"path": "context"}, EXIT_INTEGRITY)
    if not root.is_dir():
        return []
    areas: list[dict[str, Any]] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_symlink():
                raise ContextError("symlink_path", "context area cannot be a symlink", {"path": f"context/{entry.name}"}, EXIT_INTEGRITY)
            if entry.is_dir(follow_symlinks=False) and AREA_NAME.fullmatch(entry.name):
                areas.append({"area": entry.name})
    return sorted(areas, key=lambda item: item["area"])


def _scan_artifact_id(
    repo: pathlib.Path,
    identifier: str,
    areas: Sequence[dict[str, Any]],
    root_text: str | None = None,
) -> tuple[str, pathlib.Path, Document]:
    found: list[tuple[str, pathlib.Path, Document]] = []
    for area in areas:
        descriptor = _best_effort_area_descriptor(repo, area, root_text)
        for path, _ in _scan_area_paths(repo, area["area"]):
            try:
                document = parse_document(path.read_text(encoding="utf-8"), descriptor)
            except (ContextError, OSError, UnicodeError):
                continue
            if document.frontmatter["id"] == identifier:
                found.append((area["area"], path, document))
    if not found:
        raise ContextError("artifact_not_found", "artifact id was not found", {"id": identifier}, EXIT_NOT_FOUND)
    if len(found) > 1:
        raise ContextError("duplicate_id", "artifact id is duplicated", {"id": identifier}, EXIT_INTEGRITY)
    return found[0]


def _indexed_artifact_entry(
    repo: pathlib.Path,
    identifier: str,
    areas: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], pathlib.Path]:
    indexed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for area in areas:
        index_path = _ensure_contained(repo, area["path"])
        if index_path.is_symlink():
            raise ContextError("symlink_path", "area index cannot be a symlink", {"path": area["path"]}, EXIT_INTEGRITY)
        index = parse_area_index(index_path.read_text(encoding="utf-8"))
        if (
            index.frontmatter["area"] != area["area"]
            or index.frontmatter["owner"] != area["owner"]
            or index.frontmatter["artifact_schema"] != area["artifact_schema"]
            or index.frontmatter["authority"] != area["authority"]
        ):
            raise ContextError("index_stale", "area index/root catalog mismatch", {"path": area["path"]}, EXIT_INTEGRITY)
        indexed.extend(
            (area, row)
            for row in [*index.current, *index.history]
            if row["id"] == identifier
        )
    if len(indexed) != 1:
        raise ContextError("index_lookup_miss", "artifact id is absent or duplicated in derived indexes", {"id": identifier}, EXIT_INTEGRITY)
    area, row = indexed[0]
    path = _ensure_contained(repo, row["path"])
    if path.is_symlink() or not path.is_file():
        raise ContextError("index_stale", "selected index path is unavailable", {"path": row["path"]}, EXIT_INTEGRITY)
    return area, row, path


def _find_artifact(
    repo: pathlib.Path,
    identifier: str,
    *,
    warnings: list[str] | None = None,
) -> tuple[str, pathlib.Path, Document]:
    _require_context_id(identifier)
    areas: list[dict[str, Any]] = []
    root_text: str | None = None
    try:
        root_text, areas = _root_catalog(repo)
        area, row, path = _indexed_artifact_entry(repo, identifier, areas)
        index_text = _ensure_contained(repo, area["path"]).read_text(encoding="utf-8")
        descriptor = _descriptor_for_area_bytes(root_text, area, index_text)
        document = parse_document(path.read_text(encoding="utf-8"), descriptor)
        if (
            document.frontmatter["id"] != identifier
            or document.frontmatter["schema"] != area["artifact_schema"]
        ):
            raise ContextError("index_stale", "selected index entry differs from artifact identity", {"path": row["path"]}, EXIT_INTEGRITY)
        return area["area"], path, document
    except (ContextError, OSError, UnicodeError):
        if warnings is not None:
            warnings.append("index_lookup_fallback")
        if not areas:
            areas = _filesystem_lookup_areas(repo)
        return _scan_artifact_id(repo, identifier, areas, root_text)


def _frontmatter_identifier(path: pathlib.Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", newline=None) as handle:
            if handle.readline().rstrip("\r\n") != "---":
                return None
            found: str | None = None
            for line in handle:
                value = line.rstrip("\r\n")
                if value == "---":
                    return found
                if value.startswith("id: "):
                    if found is not None:
                        return None
                    parsed = json.loads(value.removeprefix("id: "))
                    if not is_context_id(parsed):
                        return None
                    found = parsed
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return None


def _artifact_id_paths(repo: pathlib.Path, identifiers: Iterable[str]) -> dict[str, list[str]]:
    wanted = set(identifiers)
    try:
        _, areas = _root_catalog(repo)
    except (ContextError, OSError, UnicodeError):
        areas = _filesystem_lookup_areas(repo)
    matches = {identifier: [] for identifier in wanted}
    for area in areas:
        for path, _ in _scan_area_paths(repo, area["area"]):
            identifier = _frontmatter_identifier(path)
            if identifier in matches:
                matches[identifier].append(path.relative_to(repo).as_posix())
    return {identifier: sorted(paths) for identifier, paths in matches.items()}


def _assert_artifact_id_unique(repo: pathlib.Path, identifier: str, selected_path: pathlib.Path) -> None:
    matches = _artifact_id_paths(repo, [identifier])[identifier]
    selected = selected_path.relative_to(repo).as_posix()
    if matches != [selected]:
        raise ContextError(
            "duplicate_id",
            "artifact id is duplicated or target identity is ambiguous",
            {"id": identifier, "paths": matches},
            EXIT_INTEGRITY,
        )


def _validate_target_artifact_ids(
    repo: pathlib.Path,
    operations: Sequence[dict[str, Any]],
    effects: Sequence[dict[str, Any]],
) -> None:
    artifact_operations = [operation for operation in operations if operation.get("role") == "artifact"]
    effect_identifiers = {effect.get("effect_id"): effect.get("id") for effect in effects}
    bound_operations: list[tuple[dict[str, Any], str]] = []
    for operation in artifact_operations:
        effect_identifier = effect_identifiers.get(operation.get("effect_id"))
        identifier = operation.get("id", effect_identifier)
        if not is_context_id(identifier) or identifier != effect_identifier:
            raise ContextError(
                "plan_preview_mismatch",
                "artifact operation id differs from its approved effect",
                exit_code=EXIT_CONFLICT,
            )
        bound_operations.append((operation, identifier))
    identifiers = {identifier for _, identifier in bound_operations}
    expected: dict[str, set[str]] = {identifier: set() for identifier in identifiers}
    for operation, identifier in bound_operations:
        op = operation["op"]
        if op == "file_create":
            relative = operation["path"]
            if _digest_or_none(_ensure_contained(repo, relative)) == operation["after_sha256"]:
                expected[identifier].add(relative)
        elif op == "file_replace":
            expected[identifier].add(operation["path"])
        elif op == "file_move":
            source = operation["from_path"]
            destination = operation["to_path"]
            if _digest_or_none(_ensure_contained(repo, source)) == operation["before_sha256"]:
                expected[identifier].add(source)
            if _digest_or_none(_ensure_contained(repo, destination)) == operation["after_sha256"]:
                expected[identifier].add(destination)
        elif op == "file_delete":
            relative = operation["path"]
            if _digest_or_none(_ensure_contained(repo, relative)) is not None:
                expected[identifier].add(relative)
    actual = _artifact_id_paths(repo, identifiers)
    for identifier in sorted(identifiers):
        expected_paths = sorted(expected[identifier])
        if actual[identifier] != expected_paths:
            raise ContextError(
                "duplicate_id",
                "target artifact id changed or is duplicated at apply time",
                {"id": identifier, "paths": actual[identifier], "expected_paths": expected_paths},
                EXIT_INTEGRITY,
            )


def build_rename_bundle(repo: pathlib.Path, identifier: str, filename: str) -> dict[str, Any]:
    warnings: list[str] = []
    area, source, document = _find_artifact(repo, identifier, warnings=warnings)
    warnings.extend(warning["code"] for warning in document.warnings)
    _assert_artifact_id_unique(repo, identifier, source)
    if area == "archive":
        raise ContextError(
            "lifecycle_unsupported",
            "archive supports only capture, read, search, and discard",
            {"area": area},
            EXIT_CONFLICT,
        )
    relative_source = source.relative_to(repo).as_posix()
    destination = resolve_artifact_path(repo, area, filename, existing_path=relative_source)
    relative_destination = destination.relative_to(repo).as_posix()
    capability = builtin_capability(area)
    request = {"schema": "context-domain-mutation-input/v1", "transition": "rename", "owner": "context-core", "target_kind": area, "requested_changes": {"filename": destination.name}, "targets": [{"id": identifier, "path": relative_source, "sha256": sha256_bytes(source.read_bytes())}], "successor_owner_result_digest": None}
    request_digest = canonical_digest(request)
    effect_id = "effect_rename_artifact"
    result = {
        "schema": "context-owner-result/v1", "result_type": "mutation", "transition": "rename", "owner": "context-core", "target_kind": area,
        "capability_digest": canonical_digest(capability),
        "semantic_inputs": [{"operation": "mutation_request", "input_schema": request["schema"], "input_digest": request_digest, "value": request}],
        "semantic_attestations": [],
        "artifact_drafts": [{"effect_id": effect_id, "path": relative_destination, "content": render_document(document.frontmatter, document.sections), "semantic_projection": {"kind": area, "primary_claim": next(iter(document.sections.values())), "supporting_context": []}}],
        "effects": [{"effect_id": effect_id, "action": "rename", "area": area, "id": identifier, "state": "history" if "/retired/" in relative_source else "current"}],
        "proposed_plan": {"schema": "context-owner-plan/v1", "transition": "rename", "operations": [{"op": "move", "effect_id": effect_id, "area": area, "id": identifier, "from_path": relative_source, "to_path": relative_destination}]},
    }
    finalized = finalize_owner_result(repo, result)
    finalized["warnings"] = sorted(set(warnings))
    return finalized


def _inbound_refs(repo: pathlib.Path, identifier: str, excluded_path: pathlib.Path) -> list[str]:
    refs: list[str] = []
    root_text, areas = _root_catalog(repo)
    for area in areas:
        index_text = _ensure_contained(repo, area["path"]).read_text(encoding="utf-8")
        descriptor = _descriptor_for_area_bytes(root_text, area, index_text)
        for path, _ in _scan_area_paths(repo, area["area"]):
            if path == excluded_path:
                continue
            try:
                document = parse_document(path.read_text(encoding="utf-8"), descriptor)
            except ContextError:
                continue
            frontmatter = document.frontmatter
            if descriptor is not None:
                if identifier in _profile_reference_ids(frontmatter, descriptor["structural_profile"]):
                    refs.append(path.relative_to(repo).as_posix())
                continue
            relation_values: list[str] = []
            for key in ("anchors", "supersedes", "superseded_by"):
                value = frontmatter.get(key, [])
                relation_values.extend(value if isinstance(value, list) else [value])
            for value in frontmatter.get("relations", {}).values() if isinstance(frontmatter.get("relations"), dict) else []:
                relation_values.extend(value if isinstance(value, list) else [value])
            if frontmatter.get("schema") == "context-observation/v1":
                evidence = _section_value(document.sections, "context-observation/v1", "Evidence")
                relation_values.extend(item for item in _list_section_items(evidence) if is_context_id(item))
            if identifier in relation_values:
                refs.append(path.relative_to(repo).as_posix())
    return sorted(refs)


def build_discard_bundle(repo: pathlib.Path, identifier: str) -> dict[str, Any]:
    warnings: list[str] = []
    area, source, document = _find_artifact(repo, identifier, warnings=warnings)
    warnings.extend(warning["code"] for warning in document.warnings)
    _assert_artifact_id_unique(repo, identifier, source)
    if area not in BUILTIN_AREAS:
        raise ContextError("owner_unavailable", "discard requires the semantic area owner", {"area": area}, EXIT_CONFLICT)
    relative = source.relative_to(repo).as_posix()
    inbound = _inbound_refs(repo, identifier, source)
    if inbound:
        raise ContextError("inbound_reference", "artifact has inbound internal references", {"paths": inbound}, EXIT_CONFLICT)
    capability = builtin_capability(area)
    request = {
        "schema": "context-domain-mutation-input/v1", "transition": "discard", "owner": "context-core", "target_kind": area,
        "requested_changes": {}, "targets": [{"id": identifier, "path": relative, "sha256": sha256_bytes(source.read_bytes())}],
        "successor_owner_result_digest": None,
    }
    effect_id = "effect_discard_artifact"
    result = {
        "schema": "context-owner-result/v1", "result_type": "mutation", "transition": "discard", "owner": "context-core", "target_kind": area,
        "capability_digest": canonical_digest(capability),
        "semantic_inputs": [{"operation": "mutation_request", "input_schema": request["schema"], "input_digest": canonical_digest(request), "value": request}],
        "semantic_attestations": [], "artifact_drafts": [],
        "effects": [{"effect_id": effect_id, "action": "delete", "area": area, "id": identifier, "state": "history" if "/retired/" in relative else "current"}],
        "proposed_plan": {"schema": "context-owner-plan/v1", "transition": "discard", "operations": [{"op": "delete", "effect_id": effect_id, "area": area, "id": identifier, "path": relative}]},
    }
    del document
    finalized = finalize_owner_result(repo, result)
    finalized["warnings"] = sorted(set(warnings))
    return finalized


def observation_read(
    repo: pathlib.Path,
    identifier: str,
    sections: Sequence[str] = (),
    max_bytes: int | None = None,
) -> dict[str, Any]:
    return _read_artifact(repo, identifier, "observation", sections, max_bytes)


def archive_read(
    repo: pathlib.Path,
    identifier: str,
    sections: Sequence[str] = (),
    max_bytes: int | None = None,
) -> dict[str, Any]:
    return _read_artifact(repo, identifier, "archive", sections, max_bytes)


def archive_search(repo: pathlib.Path, query: str, limit: int = 8) -> dict[str, Any]:
    return recall_repository(repo, query=query, areas=["archive"], include_archive=True, limit=limit)


def build_archive_discard_bundle(repo: pathlib.Path, identifier: str) -> dict[str, Any]:
    path, _ = _artifact_in_area(repo, identifier, "archive", current_only=True)
    del path
    return build_discard_bundle(repo, identifier)


def observation_search(repo: pathlib.Path, query: str = "", *, include_history: bool = False, limit: int = 8) -> dict[str, Any]:
    return recall_repository(repo, query=query, areas=["observation"], include_history=include_history, limit=limit)


def build_observation_annotate_bundle(
    repo: pathlib.Path,
    identifier: str,
    *,
    title: str | None = None,
    summary: str | None = None,
    tags: Sequence[str] | None = None,
    search_terms: Sequence[str] | None = None,
    source_refs: Sequence[str] | None = None,
    related: Sequence[str] | None = None,
    clear: Sequence[str] = (),
) -> dict[str, Any]:
    path, document = _artifact_in_area(repo, identifier, "observation", current_only=True)
    clear_set = set(clear)
    if clear_set - {"tags", "search_terms", "source_refs", "related"}:
        raise ContextError("usage_invalid", "observation clear target is invalid")
    updates = _validate_update_values(title=title, summary=summary, tags=tags, search_terms=search_terms, source_refs=source_refs)
    if related is not None:
        updates["related"] = _string_list(list(related), "related", maximum=12, item_maximum=36)
        for target in updates["related"]:
            _require_context_id(target, "related")
    frontmatter = dict(document.frontmatter)
    for field in clear_set - {"related"}:
        frontmatter.pop(field, None)
    relations = dict(frontmatter.get("relations", {}))
    if "related" in clear_set:
        relations.pop("related", None)
    if "related" in updates:
        if updates["related"]:
            relations["related"] = updates.pop("related")
        else:
            relations.pop("related", None)
    if relations:
        frontmatter["relations"] = relations
    else:
        frontmatter.pop("relations", None)
    frontmatter.update(updates)
    after = render_document(frontmatter, document.sections)
    if file_bytes(after) == path.read_bytes():
        return {"noop": True, "applied": False, "changed_paths": []}
    relative = path.relative_to(repo).as_posix()
    requested = {"metadata": updates, "clear": sorted(clear_set), "related": relations.get("related")}
    request = _mutation_request("observation_annotate", "observation", requested, [(path, document)], repo)
    effect_id = "effect_annotate_observation"
    result = _mutation_result(
        "observation",
        "observation_annotate",
        request,
        [{"effect_id": effect_id, "path": relative, "content": after, "semantic_projection": _semantic_projection("observation", Document(frontmatter, document.sections))}],
        [{"effect_id": effect_id, "action": "replace", "area": "observation", "id": identifier, "state": "current"}],
        [{"op": "replace", "effect_id": effect_id, "area": "observation", "id": identifier, "path": relative}],
    )
    return finalize_owner_result(repo, result)


def build_observation_reverify_bundle(repo: pathlib.Path, identifier: str, verified_at: str, evidence_ref: str) -> dict[str, Any]:
    path, document = _artifact_in_area(repo, identifier, "observation", current_only=True)
    _validate_timestamp(verified_at, "verified_at")
    if datetime.datetime.fromisoformat(verified_at) < datetime.datetime.fromisoformat(document.frontmatter["created_at"]):
        raise ContextError("clock_invalid", "verified_at cannot precede created_at", exit_code=EXIT_CONFLICT)
    if not _substantive(evidence_ref) or "\n" in evidence_ref or len(evidence_ref) > 500:
        raise ContextError("schema_invalid", "evidence_ref is invalid")
    frontmatter = dict(document.frontmatter)
    refs = list(frontmatter.get("source_refs", []))
    if evidence_ref not in refs:
        refs.append(evidence_ref)
    frontmatter["source_refs"] = refs
    frontmatter["verified_at"] = verified_at
    after = render_document(frontmatter, document.sections)
    if file_bytes(after) == path.read_bytes():
        return {"noop": True, "applied": False, "changed_paths": []}
    relative = path.relative_to(repo).as_posix()
    request = _mutation_request(
        "observation_reverify",
        "observation",
        {"verified_at": verified_at, "evidence_ref": evidence_ref},
        [(path, document)],
        repo,
    )
    effect_id = "effect_reverify_observation"
    result = _mutation_result(
        "observation",
        "observation_reverify",
        request,
        [{"effect_id": effect_id, "path": relative, "content": after, "semantic_projection": _semantic_projection("observation", Document(frontmatter, document.sections))}],
        [{"effect_id": effect_id, "action": "replace", "area": "observation", "id": identifier, "state": "current"}],
        [{"op": "replace", "effect_id": effect_id, "area": "observation", "id": identifier, "path": relative}],
    )
    return finalize_owner_result(repo, result)


def _history_path(path: pathlib.Path, identifier: str, repo: pathlib.Path) -> str:
    stem = path.stem
    return f"context/observation/retired/{stem}--{identifier[4:16]}.md"


def build_observation_invalidate_bundle(repo: pathlib.Path, identifier: str, reason: str, *, now: str | None = None) -> dict[str, Any]:
    path, document = _artifact_in_area(repo, identifier, "observation", current_only=True)
    if not _substantive(reason) or "\n" in reason or len(reason) > 500:
        raise ContextError("schema_invalid", "invalidation reason must be one substantive line")
    retired_at = _timestamp(now)
    frontmatter = dict(document.frontmatter)
    frontmatter.update({"retired_at": retired_at, "retired_reason": "invalidated", "retirement_note": reason.strip()})
    destination = _history_path(path, identifier, repo)
    if (repo / destination).exists():
        raise ContextError("history_path_collision", "deterministic history path already exists", {"path": destination}, EXIT_INTEGRITY)
    after = render_document(frontmatter, document.sections)
    source = path.relative_to(repo).as_posix()
    request = _mutation_request("observation_invalidate", "observation", {"reason": reason.strip(), "retired_at": retired_at}, [(path, document)], repo)
    effect_id = "effect_invalidate_observation"
    result = _mutation_result(
        "observation",
        "observation_invalidate",
        request,
        [{"effect_id": effect_id, "path": destination, "content": after, "semantic_projection": _semantic_projection("observation", Document(frontmatter, document.sections))}],
        [{"effect_id": effect_id, "action": "retire", "area": "observation", "id": identifier, "state": "history", "reason": "invalidated"}],
        [{"op": "move", "effect_id": effect_id, "area": "observation", "id": identifier, "from_path": source, "to_path": destination}],
    )
    return finalize_owner_result(repo, result)


def prepare_lifecycle_input(repo: pathlib.Path, transition: str, predecessor_id: str, successor_result: dict[str, Any]) -> dict[str, Any]:
    if transition not in {"observation_supersede", "decision_fallback_import"}:
        raise ContextError("lifecycle_transition_invalid", "unsupported lifecycle transition", {"transition": transition}, EXIT_CONFLICT)
    validate_owner_result(successor_result)
    expected_owner = "context-core" if transition == "observation_supersede" else "context-decision"
    expected_kind = "observation" if transition == "observation_supersede" else "decision"
    if (
        successor_result.get("result_type") != "claim"
        or successor_result.get("owner") != expected_owner
        or successor_result.get("target_kind") != expected_kind
        or len(successor_result.get("artifact_drafts", [])) != 1
    ):
        raise ContextError("successor_result_invalid", "lifecycle successor must be one complete claim result from the transition owner", exit_code=EXIT_CONFLICT)
    predecessor_path, predecessor = _artifact_in_area(repo, predecessor_id, "observation", current_only=True)
    successor_draft = successor_result["artifact_drafts"][0]
    successor_document = parse_document(successor_draft["content"])
    projection = successor_draft["semantic_projection"]
    claim_input = next(item for item in successor_result["semantic_inputs"] if item["operation"] == "claim")
    if transition == "decision_fallback_import" and predecessor.frontmatter.get("kind_hint") != "decision":
        raise ContextError("fallback_source_invalid", "fallback import requires a decision-like observation", exit_code=EXIT_CONFLICT)
    value = {
        "schema": "context-lifecycle-semantic-input/v1",
        "operation": "same_claim",
        "transition": transition,
        "owner": expected_owner,
        "predecessor": {
            "id": predecessor_id,
            "kind": "observation",
            "path": predecessor_path.relative_to(repo).as_posix(),
            "primary_claim": _section_value(predecessor.sections, "context-observation/v1", "Observation"),
            "artifact_sha256": sha256_bytes(predecessor_path.read_bytes()),
            "supporting_context": _semantic_projection("observation", predecessor)["supporting_context"],
        },
        "successor": {
            "id": successor_document.frontmatter["id"],
            "kind": expected_kind,
            "path": successor_draft["path"],
            "primary_claim": projection["primary_claim"],
            "artifact_sha256": sha256_bytes(file_bytes(successor_draft["content"])),
            "supporting_context": projection["supporting_context"],
        },
        "source_candidate_digest": claim_input["input_digest"],
    }
    if len(canonical_json(value).encode("utf-8")) > 4 * 1024:
        raise ContextError("lifecycle_input_too_large", "lifecycle semantic input exceeds 4 KiB", exit_code=EXIT_CONFLICT)
    return value


def build_observation_supersede_bundle(
    repo: pathlib.Path,
    predecessor_id: str,
    successor_result: dict[str, Any],
    lifecycle_input: dict[str, Any],
    lifecycle_attestation: dict[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    expected_input = prepare_lifecycle_input(repo, "observation_supersede", predecessor_id, successor_result)
    if canonical_json(lifecycle_input) != canonical_json(expected_input):
        raise ContextError("lifecycle_input_mismatch", "lifecycle input is not the exact prepared current input", exit_code=EXIT_CONFLICT)
    _validate_attestation(lifecycle_attestation, "same_claim", lifecycle_input, {"same_semantic_claim"})
    predecessor_path, predecessor = _artifact_in_area(repo, predecessor_id, "observation", current_only=True)
    successor_draft = successor_result["artifact_drafts"][0]
    successor_document = parse_document(successor_draft["content"])
    successor_id = successor_document.frontmatter["id"]
    if successor_id == predecessor_id:
        raise ContextError("successor_result_invalid", "successor id must differ from predecessor", exit_code=EXIT_CONFLICT)
    try:
        _find_artifact(repo, successor_id)
    except ContextError as error:
        if error.code != "artifact_not_found":
            raise
    else:
        raise ContextError("duplicate_id", "successor id already exists", {"id": successor_id}, EXIT_CONFLICT)
    destination = successor_draft["path"]
    existing_destination = repo / destination
    if existing_destination.exists() and existing_destination != predecessor_path:
        raise ContextError("path_exists", "successor path already exists", {"path": destination}, EXIT_CONFLICT)
    retired_at = _timestamp(now)
    predecessor_frontmatter = dict(predecessor.frontmatter)
    predecessor_frontmatter.update({"superseded_by": successor_id, "retired_at": retired_at, "retired_reason": "superseded"})
    predecessor_history_path = _history_path(predecessor_path, predecessor_id, repo)
    if (repo / predecessor_history_path).exists():
        raise ContextError("history_path_collision", "deterministic history path already exists", {"path": predecessor_history_path}, EXIT_INTEGRITY)
    successor_frontmatter = dict(successor_document.frontmatter)
    successor_frontmatter["supersedes"] = list(dict.fromkeys([*successor_frontmatter.get("supersedes", []), predecessor_id]))
    predecessor_after = render_document(predecessor_frontmatter, predecessor.sections)
    successor_after = render_document(successor_frontmatter, successor_document.sections)
    successor_result_digest = canonical_digest(successor_result)
    request = _mutation_request(
        "observation_supersede",
        "observation",
        {"predecessor": predecessor_id, "successor": successor_id, "retired_at": retired_at},
        [(predecessor_path, predecessor)],
        repo,
        successor_owner_result_digest=successor_result_digest,
        successor_artifact_sha256=sha256_bytes(file_bytes(successor_draft["content"])),
    )
    retire_effect = "effect_retire_observation"
    create_effect = "effect_create_observation"
    source = predecessor_path.relative_to(repo).as_posix()
    claim_input = next(item for item in successor_result["semantic_inputs"] if item["operation"] == "claim")
    claim_attestation = next(item for item in successor_result["semantic_attestations"] if item["operation"] == "claim")
    lifecycle_semantic_input = {"operation": "same_claim", "input_schema": lifecycle_input["schema"], "input_digest": canonical_digest(lifecycle_input), "value": lifecycle_input}
    result = _mutation_result(
        "observation",
        "observation_supersede",
        request,
        [
            {"effect_id": retire_effect, "path": predecessor_history_path, "content": predecessor_after, "semantic_projection": _semantic_projection("observation", Document(predecessor_frontmatter, predecessor.sections))},
            {"effect_id": create_effect, "path": destination, "content": successor_after, "semantic_projection": _semantic_projection("observation", Document(successor_frontmatter, successor_document.sections))},
        ],
        [
            {"effect_id": retire_effect, "action": "retire", "area": "observation", "id": predecessor_id, "state": "history", "reason": "superseded", "successor": successor_id},
            {"effect_id": create_effect, "action": "create", "area": "observation", "id": successor_id, "state": "current", "predecessor": predecessor_id},
        ],
        [
            {"op": "move", "effect_id": retire_effect, "area": "observation", "id": predecessor_id, "from_path": source, "to_path": predecessor_history_path},
            {"op": "create", "effect_id": create_effect, "area": "observation", "path": destination},
        ],
        extra_inputs=[claim_input, lifecycle_semantic_input],
        attestations=[claim_attestation, lifecycle_attestation],
    )
    return finalize_owner_result(repo, result)


def build_observation_discard_bundle(repo: pathlib.Path, identifier: str) -> dict[str, Any]:
    _artifact_in_area(repo, identifier, "observation")
    return build_discard_bundle(repo, identifier)


def _recoverable_builtin_area_specs(repo: pathlib.Path) -> list[tuple[dict[str, Any], str, str]]:
    context_root = _ensure_contained(repo, "context")
    if not context_root.is_dir() or context_root.is_symlink():
        raise ContextError("path_invalid", "context root must be a safe directory", {"path": "context"}, EXIT_INTEGRITY)
    specs: list[tuple[dict[str, Any], str, str]] = []
    for expected, label, _ in _builtin_area_specs():
        relative = expected["path"]
        path = _ensure_contained(repo, relative)
        if not path.is_file() or path.is_symlink():
            raise ContextError(
                "index_seed_required",
                "all canonical builtin area indexes are required to rebuild a missing root index",
                {"path": relative},
                EXIT_INTEGRITY,
            )
        metadata = _parse_area_index_metadata(path.read_text(encoding="utf-8"))
        if (
            metadata["area"],
            metadata["owner"],
            metadata["artifact_schema"],
            metadata["authority"],
        ) != (
            expected["area"],
            expected["owner"],
            expected["artifact_schema"],
            expected["authority"],
        ):
            raise ContextError(
                "area_index_mismatch",
                "builtin area index metadata is not authoritative enough to rebuild the root catalog",
                {"path": relative},
                EXIT_INTEGRITY,
            )
        specs.append((expected, label, metadata["summary"]))
    return sorted(specs, key=lambda item: item[0]["area"])


def repair_derived_indexes(repo: pathlib.Path) -> dict[str, Any]:
    """Rebuild derived index bytes immediately without artifact approval.

    Only generated index blocks/root catalog are writable here. Artifact bytes and
    lifecycle metadata are never changed.
    """

    changed: list[str] = []
    with _root_lock(repo):
        root_path = repo / ROOT_INDEX
        if not root_path.is_file():
            specs = _recoverable_builtin_area_specs(repo)
            if not specs:
                raise ContextError("context_root_missing", "context root has no recoverable area indexes", exit_code=EXIT_NOT_FOUND)
            _atomic_write(root_path, render_root_index(_root_seed(), specs))
            changed.append(ROOT_INDEX)

        diagnostic = refresh_repository(repo)
        _, catalog = _root_catalog(repo)
        affected = sorted({
            area["area"]
            for area in catalog
            if any(
                warning.get("code") in INDEX_FIXABLE_CODES
                and warning.get("path", "").startswith(f"context/{area['area']}/")
                for warning in diagnostic["warnings"]
            )
        })
        for area in affected:
            relative = f"context/{area}/{area}.index.md"
            rendered = render_area_index_from_repository(repo, area, repair_rows=True)
            if _digest_or_none(repo / relative) != sha256_bytes(file_bytes(rendered)):
                _atomic_write(repo / relative, rendered)
                changed.append(relative)

        warning_codes = {warning.get("code") for warning in diagnostic["warnings"]}
        if "root_index_drift" in warning_codes and "area_index_mismatch" not in warning_codes:
            specs = [_registered_area_spec(repo, row) for row in catalog]
            rendered_root = render_root_index(root_path.read_text(encoding="utf-8"), specs)
            if _digest_or_none(root_path) != sha256_bytes(file_bytes(rendered_root)):
                _atomic_write(root_path, rendered_root)
                changed.append(ROOT_INDEX)

        after = refresh_repository(repo)
    return {
        "applied": bool(changed),
        "noop": not changed,
        "changed_paths": sorted(set(changed)),
        "issues": after["issues"],
        "warnings": after["warnings"],
    }


def _core_control_error(message: str) -> None:
    raise ContextError("plan_preview_mismatch", message, exit_code=EXIT_CONFLICT)


def _require_exact_fields(value: Any, fields: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        _core_control_error(f"{label} fields differ from the core control contract")


def _valid_plan_id(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"plan_[0-9a-f]{32}", value):
        return False
    parsed = uuid.UUID(hex=value[5:])
    return parsed.version == 4 and parsed.variant == uuid.RFC_4122


def _validate_index_material(
    operation: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    relative: str,
    material_id: str,
) -> None:
    material = by_id.get(material_id)
    if (
        material is None
        or set(material) != {"material_id", "path", "content"}
        or material.get("path") != relative
        or operation["after_sha256"].get(relative) != sha256_bytes(file_bytes(material.get("content", "")))
    ):
        _core_control_error("index material path or digest is not canonical")


def _validate_core_init_control(
    plan: dict[str, Any],
    preview: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    operation: dict[str, Any],
) -> None:
    descriptor = {"owner": "context-core", "kind": "storage", "artifact_schema": PROTOCOL}
    contents = _core_init_contents()
    material_ids = {
        ROOT_INDEX: "seed_root",
        "context/snapshot/snapshot.index.md": "seed_snapshot",
        "context/observation/observation.index.md": "seed_observation",
        "context/archive/archive.index.md": "seed_archive",
    }
    control = plan["control_input"]
    _require_exact_fields(control, {"schema", "transition", "seed_digests"}, "core_init control input")
    expected_digests = {path: sha256_bytes(file_bytes(contents[path])) for path in _core_init_paths(contents)}
    if (
        plan["owner"] != "context-core"
        or plan["owner_descriptor"] != descriptor
        or control != {
            "schema": "context-core-control/v1",
            "transition": "core_init",
            "seed_digests": expected_digests,
        }
        or set(by_id) != set(material_ids.values())
        or set(operation) != {
            "op", "derived_from", "areas", "include_root", "before_sha256", "after_sha256", "seed_materials",
        }
        or operation["op"] != "index_rebuild"
        or operation["derived_from"] != ["effect_core_init"]
        or operation["areas"] != ["archive", "observation", "snapshot"]
        or operation["include_root"] is not True
        or operation["before_sha256"] != {path: None for path in contents}
        or operation["after_sha256"] != expected_digests
        or operation["seed_materials"] != material_ids
        or preview["owner"] != "context-core"
        or preview["candidate_id"] is not None
        or preview["artifacts"] != []
        or preview["effects"] != [{
            "effect_id": "effect_core_init", "action": "initialize_core", "paths": _core_init_paths(contents),
        }]
    ):
        _core_control_error("core_init plan differs from its exact allowlist")
    for relative, content in contents.items():
        _validate_index_material(operation, by_id, relative, material_ids[relative])
        if by_id[material_ids[relative]]["content"] != content:
            _core_control_error("core_init seed content is not the canonical built-in seed")


def _validate_area_register_control(
    repo: pathlib.Path,
    plan: dict[str, Any],
    preview: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    operation: dict[str, Any],
) -> None:
    descriptor = plan["owner_descriptor"]
    try:
        owner, area, _, _ = validate_owner_descriptor(descriptor)
    except ContextError as error:
        raise ContextError("plan_preview_mismatch", "area_register owner descriptor is invalid", exit_code=EXIT_CONFLICT) from error
    is_profiled = descriptor.get("schema") == "context-owner-descriptor/v2"
    expected_profile = _profile_registry_entry(descriptor) if is_profiled else None
    area_path = f"context/{area}/{area}.index.md"
    control = plan["control_input"]
    _require_exact_fields(
        control,
        {"schema", "transition", "descriptor_digest", "seed_digests"},
        "area_register control input",
    )
    if set(by_id) != {"material_root_index", "seed_area_index"}:
        _core_control_error("area_register materials differ from the exact allowlist")
    seed = by_id["seed_area_index"]
    root_material = by_id["material_root_index"]
    _require_exact_fields(seed, {"material_id", "path", "content"}, "area_register seed material")
    _require_exact_fields(root_material, {"material_id", "path", "content"}, "area_register root material")
    try:
        seed_index = parse_area_index(seed["content"])
        _, root_rows = parse_root_index(root_material["content"])
        root_profiles = parse_root_profiles(root_material["content"])
    except ContextError as error:
        raise ContextError("plan_preview_mismatch", "area_register material is not a valid canonical index", exit_code=EXIT_CONFLICT) from error
    expected_row = {
        "area": area,
        "path": area_path,
        "owner": owner,
        "claims": [area],
        "artifact_schema": descriptor["artifact_schema"],
        "authority": descriptor["authority"],
    }
    metadata = seed_index.frontmatter
    seed_descriptor = parse_area_profile(seed["content"])
    if (
        seed_index.current
        or seed_index.history
        or (metadata["area"], metadata["owner"], metadata["artifact_schema"], metadata["authority"])
        != (area, owner, descriptor["artifact_schema"], descriptor["authority"])
        or [row for row in root_rows if row["area"] == area] != [expected_row]
        or ((is_profiled and seed_descriptor != descriptor) or (not is_profiled and seed_descriptor is not None))
        or (
            (is_profiled and [item for item in root_profiles if item["area"] == area] != [expected_profile])
            or (not is_profiled and any(item["area"] == area for item in root_profiles))
        )
    ):
        _core_control_error("area_register descriptor, seed, and root row differ")
    specs: list[tuple[dict[str, Any], str, str]] = []
    for row in root_rows:
        index = seed_index if row["area"] == area else parse_area_index((repo / row["path"]).read_text(encoding="utf-8"))
        index_text = seed["content"] if row["area"] == area else index.text
        _descriptor_for_area_bytes(root_material["content"], row, index_text)
        fm = index.frontmatter
        if (fm["area"], fm["owner"], fm["artifact_schema"], fm["authority"]) != (
            row["area"], row["owner"], row["artifact_schema"], row["authority"],
        ):
            _core_control_error("area_register root and area metadata differ")
        specs.append((row, _area_label(row["area"]), fm["summary"]))
    canonical_root = render_root_index(root_material["content"], specs)
    canonical_root = render_root_profiles(canonical_root, root_profiles)
    if canonical_root != root_material["content"]:
        _core_control_error("area_register root generated bytes are not canonical")
    current_root_digest = _digest_or_none(repo / ROOT_INDEX)
    if current_root_digest == operation.get("before_sha256", {}).get(ROOT_INDEX):
        current_root = (repo / ROOT_INDEX).read_text(encoding="utf-8")
        _, current_rows = parse_root_index(current_root)
        current_profiles = parse_root_profiles(current_root)
        expected_profiles = sorted(
            [*current_profiles, *([expected_profile] if expected_profile is not None else [])],
            key=lambda item: item["area"],
        )
        expected_root = render_root_index(current_root, specs)
        expected_root = render_root_profiles(expected_root, expected_profiles)
        if (
            root_rows != sorted([*current_rows, expected_row], key=lambda row: row["area"])
            or root_profiles != expected_profiles
            or root_material["content"] != expected_root
        ):
            _core_control_error("area_register root material does not add exactly one area")
    elif current_root_digest == operation.get("after_sha256", {}).get(ROOT_INDEX):
        prior_specs = [spec for spec in specs if spec[0]["area"] != area]
        prior_root = render_root_index(root_material["content"], prior_specs)
        prior_profiles = [item for item in root_profiles if item["area"] != area]
        prior_root = render_root_profiles(prior_root, prior_profiles)
        reconstructed_root = render_root_index(prior_root, specs)
        reconstructed_root = render_root_profiles(reconstructed_root, root_profiles)
        if (
            operation.get("before_sha256", {}).get(ROOT_INDEX)
            != sha256_bytes(file_bytes(prior_root))
            or reconstructed_root != root_material["content"]
        ):
            _core_control_error("area_register resume state is not the exact canonical write prefix")
    seed_digest = sha256_bytes(file_bytes(seed["content"]))
    if (
        plan["owner"] != owner
        or control != {
            "schema": "context-core-control/v1",
            "transition": "area_register",
            "descriptor_digest": canonical_digest(descriptor),
            "seed_digests": {area_path: seed_digest},
        }
        or set(operation) != {
            "op", "derived_from", "areas", "include_root", "before_sha256", "after_sha256", "seed_materials",
        }
        or operation["op"] != "index_rebuild"
        or operation["derived_from"] != ["effect_register_area"]
        or operation["areas"] != [area]
        or operation["include_root"] is not True
        or set(operation["before_sha256"]) != {ROOT_INDEX, area_path}
        or operation["before_sha256"].get(area_path) not in {None, seed_digest}
        or set(operation["after_sha256"]) != {ROOT_INDEX, area_path}
        or operation["seed_materials"] != {area_path: "seed_area_index"}
        or root_material["path"] != ROOT_INDEX
        or seed["path"] != area_path
        or preview["owner"] != owner
        or preview["candidate_id"] is not None
        or preview["artifacts"] != []
        or preview["effects"] != [{
            "effect_id": "effect_register_area", "action": "register_area", "area": area, "path": area_path,
        }]
    ):
        _core_control_error("area_register plan differs from its exact allowlist")
    _validate_index_material(operation, by_id, ROOT_INDEX, "material_root_index")
    _validate_index_material(operation, by_id, area_path, "seed_area_index")


def _validate_policy_control(
    repo: pathlib.Path,
    plan: dict[str, Any],
    preview: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    operation: dict[str, Any],
) -> None:
    control = plan["control_input"]
    _require_exact_fields(
        control,
        {"schema", "transition", "target", "before_sha256", "outside_bytes_sha256"},
        "policy_install control input",
    )
    target = control.get("target")
    spec = MANAGED_ROOT_FILES.get(target) if isinstance(target, str) else None
    if spec is None or plan.get("transition") != spec["transition"]:
        _core_control_error("managed root file target or transition is not in the allowlist")
    descriptor = dict(spec["descriptor"])
    POLICY_BEGIN, POLICY_END, POLICY_BODY = spec["begin"], spec["end"], spec["body"]
    if set(by_id) != {spec["material_id"]}:
        _core_control_error("policy_install material differs from the exact allowlist")
    material = by_id[spec["material_id"]]
    _require_exact_fields(material, {"material_id", "path", "content"}, "policy material")
    content = material["content"]
    newline = "\r\n" if "\r\n" in content else "\n"
    if "\r\n" in content and "\n" in content.replace("\r\n", ""):
        _core_control_error("policy material has mixed newlines")
    expected_block = POLICY_BODY.replace("\n", newline)
    start = content.find(POLICY_BEGIN)
    end = content.find(POLICY_END, start) + len(POLICY_END)
    if content.count(POLICY_BEGIN) != 1 or content.count(POLICY_END) != 1 or content[start:end] != expected_block:
        _core_control_error("policy material managed block is not canonical")
    current = _ensure_contained(repo, str(target))
    current_digest = _digest_or_none(current)
    if current_digest == operation.get("before_sha256"):
        before = current.read_text(encoding="utf-8") if current_digest is not None else ""
        outside = (
            before[:before.find(POLICY_BEGIN)] + before[before.find(POLICY_END) + len(POLICY_END):]
            if POLICY_BEGIN in before
            else before
        )
        if control.get("outside_bytes_sha256") != sha256_bytes(outside.encode("utf-8")):
            _core_control_error("policy control input does not bind marker-external bytes")
        before_newline = "\r\n" if "\r\n" in before else "\n"
        policy_body = POLICY_BODY.replace("\n", before_newline)
        if POLICY_BEGIN in before:
            before_start = before.index(POLICY_BEGIN)
            before_end = before.index(POLICY_END, before_start) + len(POLICY_END)
            expected_content = before[:before_start] + policy_body + before[before_end:]
        elif before:
            separator = "" if before.endswith(before_newline * 2) else (before_newline if before.endswith(before_newline) else before_newline * 2)
            expected_content = before + separator + policy_body + before_newline
        else:
            expected_content = policy_body + before_newline
        if content != expected_content:
            _core_control_error("policy material changes marker-external bytes")
    expected_op = "file_replace" if operation.get("before_sha256") is not None else "file_create"
    if (
        plan["owner"] != "context-core"
        or plan["owner_descriptor"] != descriptor
        or control.get("schema") != "context-core-control/v1"
        or control.get("transition") != spec["transition"]
        or target not in MANAGED_ROOT_FILES
        or control.get("before_sha256") != operation.get("before_sha256")
        or set(operation) != {"op", "effect_id", "role", "path", "before_sha256", "after_sha256", "material"}
        or operation["op"] != expected_op
        or operation["effect_id"] != spec["effect_id"]
        or operation["role"] != "policy"
        or operation["path"] != target
        or operation["material"] != spec["material_id"]
        or operation["after_sha256"] != sha256_bytes(content.encode("utf-8"))
        or material["path"] != target
        or preview["owner"] != "context-core"
        or preview["candidate_id"] is not None
        or preview["artifacts"] != [{
            "effect_id": spec["effect_id"], "path": target, "content": content,
        }]
        or preview["effects"] != [{
            "effect_id": spec["effect_id"], "action": spec["action"], "path": target,
        }]
    ):
        _core_control_error("policy_install plan differs from its exact allowlist")


def _validate_core_control_bundle(
    repo: pathlib.Path,
    plan: dict[str, Any],
    preview: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    non_index: list[dict[str, Any]],
    index_operations: list[dict[str, Any]],
) -> None:
    _require_exact_fields(
        plan,
        {
            "schema", "plan_id", "owner", "source_type", "transition", "owner_descriptor", "control_input",
            "prior_bundle_digests", "read_preconditions", "operations",
        },
        "core control plan",
    )
    transition = plan.get("transition")
    expected_preview_fields = {"schema", "owner", "candidate_id", "artifacts", "effects"}
    _require_exact_fields(preview, expected_preview_fields, "core control preview")
    if (
        plan.get("schema") != "context-mutation-plan/v1"
        or plan.get("source_type") != "core_control"
        or transition not in {"core_init", "area_register", *MANAGED_ROOT_TRANSITIONS}
        or not _valid_plan_id(plan.get("plan_id"))
        or plan.get("prior_bundle_digests") != []
        or plan.get("read_preconditions") != []
        or preview.get("schema") != "context-approval-preview/v1"
        or not isinstance(preview.get("artifacts"), list)
        or not isinstance(preview.get("effects"), list)
        or any(set(material) != {"material_id", "path", "content"} for material in by_id.values())
    ):
        _core_control_error("core control envelope differs from the exact allowlist")
    if transition in MANAGED_ROOT_TRANSITIONS:
        if index_operations or len(non_index) != 1:
            _core_control_error("policy_install permits exactly one policy file operation")
        _validate_policy_control(repo, plan, preview, by_id, non_index[0])
        return
    if non_index or len(index_operations) != 1:
        _core_control_error(f"{transition} permits exactly one index_rebuild operation")
    operation = index_operations[0]
    if transition == "core_init":
        _validate_core_init_control(plan, preview, by_id, operation)
    else:
        _validate_area_register_control(repo, plan, preview, by_id, operation)


def _validate_bundle(repo: pathlib.Path, bundle: dict[str, Any], approved_digest: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if bundle.get("schema") != "context-mutation-bundle/v1":
        raise ContextError("bundle_invalid", "mutation bundle schema is invalid", exit_code=EXIT_CONFLICT)
    actual = canonical_digest(bundle.get("approval_material"))
    if approved_digest != bundle.get("approval_digest") or actual != bundle.get("approval_digest"):
        raise ContextError("approval_digest_mismatch", "approved digest does not match the immutable final bundle", exit_code=EXIT_CONFLICT)
    _require_vault_identity(repo, bundle.get("approval_material"))
    materials = bundle.get("materials", [])
    by_id = {item.get("material_id"): item for item in materials}
    if len(by_id) != len(materials) or any(not LOCAL_ID.fullmatch(str(key)) for key in by_id):
        raise ContextError("plan_preview_mismatch", "material ids are invalid or duplicate", exit_code=EXIT_CONFLICT)
    plan = bundle["approval_material"].get("plan", {})
    if plan.get("source_type") == "owner_result":
        if bundle["approval_material"].get("project_policy") != _config_call("binding", sys.modules[__name__], repo):
            raise ContextError("project_policy_changed", "Project settings or policy runtime changed; prepare a fresh record.", exit_code=EXIT_CONFLICT)
    preview = bundle["approval_material"].get("preview", {})
    if plan.get("schema") != "context-mutation-plan/v1" or preview.get("schema") != "context-approval-preview/v1":
        raise ContextError("bundle_invalid", "approval material is incomplete", exit_code=EXIT_CONFLICT)
    plan_operations = plan.get("operations", [])
    for precondition in plan.get("read_preconditions", []):
        if set(precondition) != {"id", "path", "sha256"}:
            raise ContextError("read_precondition_invalid", "final plan read precondition shape is invalid", exit_code=EXIT_CONFLICT)
        target = _ensure_contained(repo, precondition["path"])
        completed_move = next(
            (
                operation
                for operation in plan_operations
                if operation.get("op") == "file_move"
                and operation.get("id") == precondition["id"]
                and operation.get("from_path") == precondition["path"]
                and not target.exists()
                and _digest_or_none(_ensure_contained(repo, operation["to_path"])) == operation.get("after_sha256")
            ),
            None,
        )
        if completed_move is None and (not target.is_file() or sha256_bytes(target.read_bytes()) != precondition["sha256"]):
            raise ContextError("precondition_changed", "final plan read precondition is stale", {"path": precondition["path"]}, EXIT_CONFLICT)
    operations = plan_operations
    non_index = [operation for operation in operations if operation.get("op") != "index_rebuild"]
    effect_ids = [operation.get("effect_id") for operation in non_index]
    preview_ids = [effect.get("effect_id") for effect in preview.get("effects", [])]
    preview_artifacts = {artifact.get("effect_id"): artifact for artifact in preview.get("artifacts", [])}
    if len(preview_artifacts) != len(preview.get("artifacts", [])):
        raise ContextError("plan_preview_mismatch", "preview artifact effect ids are duplicate", exit_code=EXIT_CONFLICT)
    if len(effect_ids) != len(set(effect_ids)):
        raise ContextError("plan_preview_mismatch", "operations and preview effects are not 1:1", exit_code=EXIT_CONFLICT)
    index_operations = [operation for operation in operations if operation.get("op") == "index_rebuild"]
    derived_ids = index_operations[0].get("derived_from", []) if len(index_operations) == 1 else []
    if plan.get("transition") in MANAGED_ROOT_TRANSITIONS:
        if index_operations or len(non_index) != 1 or set(effect_ids) != set(preview_ids):
            raise ContextError("plan_preview_mismatch", "policy install must contain one visible file operation", exit_code=EXIT_CONFLICT)
    elif (
        len(index_operations) != 1
        or len(derived_ids) != len(set(derived_ids))
        or set(effect_ids) | set(derived_ids) != set(preview_ids)
        or not set(effect_ids).issubset(set(derived_ids))
    ):
        raise ContextError("plan_preview_mismatch", "index rebuild does not cover preview effects", exit_code=EXIT_CONFLICT)
    for operation in non_index:
        if operation.get("op") not in {"file_create", "file_replace", "file_move", "file_delete"}:
            raise ContextError("plan_preview_mismatch", "physical operation is not allowed", exit_code=EXIT_CONFLICT)
        role = operation.get("role")
        if role not in {"artifact", "policy"}:
            raise ContextError("plan_preview_mismatch", "file operation role is invalid", exit_code=EXIT_CONFLICT)
        if role == "policy" and (plan.get("transition") not in MANAGED_ROOT_TRANSITIONS or operation.get("path") not in MANAGED_ROOT_FILES or "area" in operation):
            raise ContextError("policy_target_invalid", "policy operation escapes the repository-root allowlist", exit_code=EXIT_CONFLICT)
        if role == "artifact" and not isinstance(operation.get("area"), str):
            raise ContextError("plan_preview_mismatch", "artifact operation lacks area", exit_code=EXIT_CONFLICT)
        material_id = operation.get("material")
        if operation["op"] != "file_delete" and operation.get("after_sha256") != operation.get("before_sha256") and material_id not in by_id:
            raise ContextError("material_digest_mismatch", "file operation material is missing", exit_code=EXIT_CONFLICT)
        if material_id:
            content = by_id[material_id]["content"]
            material_bytes = content.encode("utf-8") if role == "policy" else file_bytes(content)
            if sha256_bytes(material_bytes) != operation["after_sha256"]:
                raise ContextError("material_digest_mismatch", "material bytes do not match after digest", {"material_id": material_id}, EXIT_CONFLICT)
        artifact = preview_artifacts.get(operation["effect_id"])
        if operation["op"] == "file_delete":
            if artifact is not None:
                raise ContextError("plan_preview_mismatch", "delete operation must not hide a destination draft", exit_code=EXIT_CONFLICT)
        else:
            expected_path = operation.get("to_path", operation.get("path"))
            if artifact is None or artifact.get("path") != expected_path:
                raise ContextError("plan_preview_mismatch", "physical destination differs from grouped preview", exit_code=EXIT_CONFLICT)
            artifact_bytes = artifact.get("content", "").encode("utf-8") if role == "policy" else file_bytes(artifact.get("content", ""))
            if sha256_bytes(artifact_bytes) != operation.get("after_sha256"):
                raise ContextError("plan_preview_mismatch", "preview content differs from exact apply bytes", exit_code=EXIT_CONFLICT)
    if plan.get("source_type") == "owner_result":
        owner_material = by_id.get(plan.get("owner_result_material"))
        if owner_material is None or owner_material.get("path") is not None or sha256_bytes(owner_material["content"].encode("utf-8")) != plan.get("owner_result_digest"):
            raise ContextError("material_digest_mismatch", "owner result material is invalid", exit_code=EXIT_CONFLICT)
        try:
            owner_result = json.loads(owner_material["content"])
        except json.JSONDecodeError as error:
            raise ContextError("owner_result_invalid", "owner result material is not JSON", exit_code=EXIT_CONFLICT) from error
        validation = plan.get("owner_validation")
        registered_row, registered_index, registered_descriptor = _profiled_area_for_owner(
            repo,
            plan.get("owner_descriptor", {}).get("kind"),
            plan.get("owner"),
        )
        capability: dict[str, Any] | None = None
        transition_topology: str | None = None
        if registered_descriptor is not None:
            all_prior_digests = plan.get("prior_bundle_digests")
            same_area_prior_digests = plan.get("prior_same_area_bundle_digests")
            valid_prior_shape = (
                isinstance(all_prior_digests, list)
                and isinstance(same_area_prior_digests, list)
                and all(
                    isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                    for digest in [*all_prior_digests, *same_area_prior_digests]
                )
                and len(all_prior_digests) == len(set(all_prior_digests))
                and len(same_area_prior_digests) == len(set(same_area_prior_digests))
                and [
                    digest
                    for digest in all_prior_digests
                    if digest in set(same_area_prior_digests)
                ] == same_area_prior_digests
            )
            if (
                plan.get("owner_descriptor") != registered_descriptor
                or plan.get("descriptor_digest") != canonical_digest(registered_descriptor)
                or plan.get("transition_topology") not in PROFILE_TOPOLOGIES
                or not isinstance(validation, dict)
                or not valid_prior_shape
                or validation.get("prior_same_area_bundle_digests") != same_area_prior_digests
            ):
                raise ContextError("descriptor_digest_mismatch", "final plan is not bound to the registered structural descriptor", exit_code=EXIT_CONFLICT)
            capability, transition_topology = _validate_owner_validation(
                owner_result,
                validation,
                registered_index,
                same_area_prior_digests,
                registered_descriptor,
                base_area_index_sha256=index_operations[0].get("before_sha256", {}).get(registered_row["path"]),
                verify_base_area_index=not bool(same_area_prior_digests),
            )
        validate_owner_result(owner_result, capability, registered_descriptor)
        _validate_typed_relations(repo, owner_result, registered_descriptor)
        if registered_descriptor is not None:
            assert transition_topology is not None
            _validate_profiled_owner_structure(repo, owner_result, registered_descriptor, transition_topology)
        if canonical_json(owner_result) != owner_material["content"]:
            raise ContextError("owner_result_invalid", "owner result material is not canonical JSON", exit_code=EXIT_CONFLICT)
        if (
            owner_result["transition"] != plan.get("transition")
            or owner_result["owner"] != plan.get("owner")
            or owner_result["capability_digest"] != plan.get("capability_digest")
        ):
            raise ContextError("plan_preview_mismatch", "final plan is not bound to its owner result", exit_code=EXIT_CONFLICT)
        if registered_row["artifact_schema"] != plan["owner_descriptor"].get("artifact_schema"):
            raise ContextError("plan_preview_mismatch", "final plan artifact schema differs from registered area", exit_code=EXIT_CONFLICT)
        _validate_target_artifact_ids(repo, non_index, owner_result["effects"])
        if owner_result["owner"] != "context-core":
            if not isinstance(validation, dict):
                raise ContextError("owner_validation_required", "addon final plan lacks an owner validation receipt", exit_code=EXIT_CONFLICT)
            if registered_descriptor is None:
                receipt_body = dict(validation)
                receipt_digest = receipt_body.pop("receipt_digest", None)
                if (
                    validation.get("schema") != "context-owner-validation-receipt/v1"
                    or validation.get("owner") != owner_result["owner"]
                    or validation.get("kind") != owner_result["target_kind"]
                    or validation.get("owner_result_digest") != canonical_digest(owner_result)
                    or validation.get("status") != "valid"
                    or receipt_digest != canonical_digest(receipt_body)
                ):
                    raise ContextError("owner_validation_invalid", "addon owner validation receipt is altered", exit_code=EXIT_CONFLICT)
        index_operation = index_operations[0]
        index_paths = {
            f"context/{area}/{area}.index.md"
            for area in index_operation.get("areas", [])
        }
        index_materials = {
            item.get("path"): item
            for item in materials
            if item.get("path") in index_paths
        }
        if set(index_materials) != index_paths:
            raise ContextError(
                "material_digest_mismatch",
                "owner result must bind the exact target area index bytes",
                exit_code=EXIT_CONFLICT,
            )
        owner_effects = {effect["effect_id"]: effect for effect in owner_result["effects"]}
        owner_drafts = {draft["effect_id"]: draft for draft in owner_result["artifact_drafts"]}
        for area in index_operation["areas"]:
            relative = f"context/{area}/{area}.index.md"
            material = index_materials[relative]
            content = material.get("content", "")
            if (
                sha256_bytes(file_bytes(content)) != index_operation["after_sha256"].get(relative)
                or parse_area_index(content).frontmatter.get("area") != area
            ):
                raise ContextError(
                    "material_digest_mismatch",
                    "target area index material differs from the approved digest",
                    {"path": relative},
                    EXIT_CONFLICT,
                )
            current_digest = _digest_or_none(repo / relative)
            before_digest = index_operation["before_sha256"].get(relative)
            after_digest = index_operation["after_sha256"].get(relative)
            if current_digest == before_digest:
                current_index = _tolerant_area_index(repo, area, (repo / relative).read_text(encoding="utf-8"))
                matching_effects = [effect for effect in owner_effects.values() if effect.get("area") == area]
                matching_drafts = {
                    effect_id: draft
                    for effect_id, draft in owner_drafts.items()
                    if any(effect["effect_id"] == effect_id for effect in matching_effects)
                }
                area_descriptor = registered_descriptor if area == registered_row["area"] else None
                if content != _virtual_area_index(current_index, matching_effects, matching_drafts, area_descriptor):
                    raise ContextError(
                        "plan_preview_mismatch",
                        "target area index material is not derived from the approved effects",
                        {"path": relative},
                        EXIT_CONFLICT,
                    )
            elif current_digest != after_digest:
                if plan.get("prior_same_area_bundle_digests"):
                    # A chained proposal was validated on the virtual index after its priors;
                    # it is only meaningful once those priors are physically applied.
                    raise ContextError(
                        "precondition_changed",
                        "target area index precondition changed",
                        {"path": relative},
                        EXIT_CONFLICT,
                    )
                area_descriptor = registered_descriptor if area == registered_row["area"] else None
                _require_benign_index_drift(repo, area, content, list(owner_effects.values()), owner_drafts, area_descriptor)
        request_inputs = [item for item in owner_result["semantic_inputs"] if item.get("operation") == "mutation_request"]
        if request_inputs:
            request = request_inputs[0]["value"]
            for target in request["targets"]:
                matching_operations = [
                    operation
                    for operation in non_index
                    if operation.get("from_path", operation.get("path")) == target["path"]
                    and operation.get("id") == target["id"]
                    and operation.get("before_sha256") == target["sha256"]
                ]
                if len(matching_operations) != 1:
                    raise ContextError("plan_preview_mismatch", "mutation target is not exact in the physical plan", {"path": target["path"]}, EXIT_CONFLICT)
        if owner_result["transition"] == "observation_supersede":
            inputs = {item["operation"]: item["value"] for item in owner_result["semantic_inputs"]}
            lifecycle = inputs["same_claim"]
            request = inputs["mutation_request"]
            drafts = {draft["effect_id"]: draft for draft in owner_result["artifact_drafts"]}
            effects = {effect["effect_id"]: effect for effect in owner_result["effects"]}
            create_effects = [effect for effect in effects.values() if effect.get("action") == "create"]
            retire_effects = [effect for effect in effects.values() if effect.get("action") == "retire"]
            if len(create_effects) != 1 or len(retire_effects) != 1:
                raise ContextError("plan_preview_mismatch", "observation supersede must contain one create and one retire", exit_code=EXIT_CONFLICT)
            created = parse_document(drafts[create_effects[0]["effect_id"]]["content"])
            retired = parse_document(drafts[retire_effects[0]["effect_id"]]["content"])
            successor_id = created.frontmatter["id"]
            predecessor_id = retired.frontmatter["id"]
            predecessor_target = request["targets"][0] if len(request["targets"]) == 1 else {}
            if (
                lifecycle["source_candidate_digest"] != next(item["input_digest"] for item in owner_result["semantic_inputs"] if item["operation"] == "claim")
                or lifecycle["predecessor"]["id"] != predecessor_id
                or lifecycle["predecessor"]["path"] != predecessor_target.get("path")
                or lifecycle["predecessor"]["primary_claim"] != _section_value(retired.sections, "context-observation/v1", "Observation")
                or lifecycle["predecessor"]["artifact_sha256"] != predecessor_target.get("sha256")
                or lifecycle["successor"]["id"] != successor_id
                or lifecycle["successor"]["path"] != drafts[create_effects[0]["effect_id"]]["path"]
                or lifecycle["successor"]["primary_claim"] != _section_value(created.sections, "context-observation/v1", "Observation")
                or lifecycle["successor"]["artifact_sha256"] != request.get("successor_artifact_sha256")
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(request.get("successor_artifact_sha256")))
                or request["requested_changes"].get("predecessor") != predecessor_id
                or request["requested_changes"].get("successor") != successor_id
                or retired.frontmatter.get("superseded_by") != successor_id
                or predecessor_id not in created.frontmatter.get("supersedes", [])
            ):
                raise ContextError("lifecycle_input_mismatch", "supersede lifecycle inputs and reciprocal artifact edges differ", exit_code=EXIT_CONFLICT)
        if owner_result["transition"] == "decision_fallback_import":
            inputs = {item["operation"]: item["value"] for item in owner_result["semantic_inputs"]}
            lifecycle = inputs["same_claim"]
            request = inputs["mutation_request"]
            drafts = {draft["effect_id"]: draft for draft in owner_result["artifact_drafts"]}
            dec_effects = [effect for effect in owner_result["effects"] if effect.get("action") == "create" and effect.get("area") == "decision"]
            obs_effects = [effect for effect in owner_result["effects"] if effect.get("action") == "retire" and effect.get("area") == "observation"]
            if len(dec_effects) != 1 or len(obs_effects) != 1:
                raise ContextError("plan_preview_mismatch", "fallback import must create one DEC and retire one OBS", exit_code=EXIT_CONFLICT)
            created = parse_document(drafts[dec_effects[0]["effect_id"]]["content"])
            retired = parse_document(drafts[obs_effects[0]["effect_id"]]["content"])
            target = request["targets"][0] if len(request.get("targets", [])) == 1 else {}
            if (
                lifecycle.get("source_candidate_digest") != next(item["input_digest"] for item in owner_result["semantic_inputs"] if item["operation"] == "claim")
                or lifecycle.get("predecessor", {}).get("kind") != "observation"
                or lifecycle.get("successor", {}).get("kind") != "decision"
                or lifecycle.get("predecessor", {}).get("id") != retired.frontmatter["id"]
                or lifecycle.get("predecessor", {}).get("path") != target.get("path")
                or lifecycle.get("predecessor", {}).get("primary_claim") != _section_value(retired.sections, "context-observation/v1", "Observation")
                or lifecycle.get("predecessor", {}).get("artifact_sha256") != target.get("sha256")
                or lifecycle.get("successor", {}).get("id") != created.frontmatter["id"]
                or lifecycle.get("successor", {}).get("path") != drafts[dec_effects[0]["effect_id"]]["path"]
                or lifecycle.get("successor", {}).get("primary_claim") != _section_value(created.sections, "context-decision/v1", "Decision")
                or lifecycle.get("successor", {}).get("artifact_sha256") != request.get("successor_artifact_sha256")
                or retired.frontmatter.get("kind_hint") != "decision"
                or retired.frontmatter.get("superseded_by") != created.frontmatter["id"]
                or retired.frontmatter["id"] not in created.frontmatter.get("supersedes", [])
                or retired.frontmatter["id"] in created.frontmatter.get("relations", {}).get("informed_by", [])
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(request.get("successor_owner_result_digest")))
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(request.get("successor_artifact_sha256")))
            ):
                raise ContextError("lifecycle_input_mismatch", "fallback import lifecycle and attested artifact inputs differ", exit_code=EXIT_CONFLICT)
    elif plan.get("source_type") != "core_control":
        raise ContextError("bundle_invalid", "source_type is unsupported", exit_code=EXIT_CONFLICT)
    else:
        _validate_core_control_bundle(repo, plan, preview, by_id, non_index, index_operations)
    return plan, by_id


@contextlib.contextmanager
def _root_lock(repo: pathlib.Path) -> Iterator[None]:
    lock_root = pathlib.Path(tempfile.gettempdir()) / "context-core-locks"
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(lock_root, 0o700)
    name = hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_root / name, flags, 0o600)
    try:
        mode = os.fstat(fd).st_mode & 0o777
        if mode & 0o022:
            raise ContextError("lock_unsafe", "lock file is group/other writable", exit_code=EXIT_CONFLICT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _atomic_write(path: pathlib.Path, content: str) -> None:
    _atomic_write_bytes(path, file_bytes(content))


def _atomic_write_bytes(path: pathlib.Path, content: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".context-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        with contextlib.suppress(OSError):
            parent_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)


def _digest_or_none(path: pathlib.Path) -> str | None:
    return sha256_bytes(path.read_bytes()) if path.is_file() else None


def _require_benign_index_drift(
    repo: pathlib.Path,
    area: str,
    material_content: str,
    effects: Sequence[dict[str, Any]],
    drafts: dict[str, dict[str, Any]],
    descriptor: dict[str, Any] | None,
) -> None:
    """Decide whether a derived index that changed since preview may be re-derived at apply.

    The index is a projection, so its bytes are not part of the approved payload. Index-only
    drift (row order, duplicates, unrelated additions) passes and the index is regenerated
    under the root lock. The write is still refused when a Current decision this plan never
    saw now occupies the same or an overlapping slot as a decision the plan makes Current, or
    when that slot already holds two Current decisions (a merge left them; doctor reports it).
    Artifact preconditions and read preconditions are checked separately and unchanged.
    """
    seen = {row["id"] for row in parse_area_index(material_content).current}
    planned: list[tuple[str, str, str]] = []
    for effect in effects:
        if effect.get("area") != area:
            continue
        draft = drafts.get(effect.get("effect_id"))
        if draft is None or "/retired/" in draft["path"]:
            continue
        frontmatter = parse_document(draft["content"], descriptor).frontmatter
        if frontmatter.get("schema") != "context-decision/v1":
            continue
        planned.append((
            _canonical_decision_scope(frontmatter.get("scope", "")),
            _canonical_decision_key(frontmatter.get("decision_key", "")),
            frontmatter["id"],
        ))
    if not planned:
        return
    planned_ids = {identifier for _, _, identifier in planned}
    occupants: dict[tuple[str, str], list[str]] = {}
    unseen: list[tuple[tuple[str, str], str, str]] = []
    for path, _ in _scan_area_paths(repo, area, include_history=False):
        try:
            frontmatter = parse_document(path.read_text(encoding="utf-8"), descriptor).frontmatter
        except (ContextError, OSError, UnicodeError):
            continue
        if frontmatter.get("schema") != "context-decision/v1":
            continue
        slot = (
            _canonical_decision_scope(frontmatter.get("scope", "")),
            _canonical_decision_key(frontmatter.get("decision_key", "")),
        )
        identifier = frontmatter["id"]
        occupants.setdefault(slot, []).append(identifier)
        if identifier not in seen and identifier not in planned_ids:
            unseen.append((slot, identifier, path.relative_to(repo).as_posix()))
    for scope, key, identifier in planned:
        others = [item for item in occupants.get((scope, key), []) if item != identifier]
        if len(others) >= 2:
            raise ContextError(
                "precondition_changed",
                "target slot holds two current decisions; withdraw or supersede one before writing",
                {"area": area, "scope": scope, "decision_key": key, "ids": sorted(others)},
                EXIT_CONFLICT,
            )
        for (other_scope, other_key), other_id, other_path in unseen:
            if other_key == key and _decision_scopes_overlap(other_scope, scope):
                raise ContextError(
                    "precondition_changed",
                    "a current decision in the same or an overlapping slot was added after preview",
                    {"area": area, "path": other_path, "id": other_id, "scope": other_scope, "decision_key": other_key},
                    EXIT_CONFLICT,
                )


def _plan_touched_paths(plan: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for operation in plan.get("operations", []):
        if operation.get("op") == "index_rebuild":
            paths.extend(f"context/{area}/{area}.index.md" for area in operation.get("areas", []))
            paths.extend(path for path in operation.get("after_sha256", {}) if isinstance(path, str))
            if operation.get("include_root"):
                paths.append(ROOT_INDEX)
        else:
            paths.extend(operation.get(key) for key in ("path", "from_path", "to_path") if isinstance(operation.get(key), str))
    ordered: list[str] = []
    for path in paths:
        if path not in ordered:
            ordered.append(path)
    return ordered


def _journal_paths(repo: pathlib.Path, relatives: Sequence[str]) -> dict[str, tuple[bytes, int] | None]:
    journal: dict[str, tuple[bytes, int] | None] = {}
    for relative in relatives:
        path = _ensure_contained(repo, relative)
        journal[relative] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.is_file() else None
    return journal


def _restore_journal(repo: pathlib.Path, journal: dict[str, tuple[bytes, int] | None]) -> None:
    """Best-effort rollback of an interrupted apply: every touched path returns to its journaled bytes."""
    for relative, saved in reversed(list(journal.items())):
        path = repo / relative
        if saved is None:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            continue
        content, mode = saved
        if not path.is_file() or path.read_bytes() != content:
            _atomic_write_bytes(path, content, mode=mode)


def _apply_file_operation(
    repo: pathlib.Path,
    operation: dict[str, Any],
    materials: dict[str, dict[str, Any]],
    changed: list[str],
    descriptor: dict[str, Any] | None = None,
) -> None:
    op = operation["op"]
    if op in {"file_create", "file_replace"}:
        path = _ensure_contained(repo, operation["path"])
        current = _digest_or_none(path)
        if current == operation["after_sha256"]:
            return
        if current != operation["before_sha256"]:
            raise ContextError("precondition_changed", "file precondition changed", {"path": operation["path"]}, EXIT_CONFLICT)
        if op == "file_replace" and operation.get("role") == "artifact" and parse_document(path.read_text(encoding="utf-8"), descriptor).frontmatter["id"] != operation.get("id"):
            raise ContextError("precondition_changed", "replace target id changed", {"path": operation["path"]}, EXIT_CONFLICT)
        content = materials[operation["material"]]["content"]
        if operation.get("role") == "policy":
            path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
            _atomic_write_bytes(path, content.encode("utf-8"), mode=mode)
        else:
            _atomic_write(path, content)
        changed.append(operation["path"])
    elif op == "file_move":
        source = _ensure_contained(repo, operation["from_path"])
        destination = _ensure_contained(repo, operation["to_path"])
        source_digest = _digest_or_none(source)
        destination_digest = _digest_or_none(destination)
        before = operation["before_sha256"]
        after = operation["after_sha256"]
        material = operation.get("material")
        if source_digest is None and destination_digest == after:
            return
        if source_digest == before and parse_document(source.read_text(encoding="utf-8"), descriptor).frontmatter["id"] != operation.get("id"):
            raise ContextError("precondition_changed", "move source id changed", {"path": operation["from_path"]}, EXIT_CONFLICT)
        if material is None:
            if source_digest != before or destination_digest is not None:
                raise ContextError("precondition_changed", "rename state is invalid", exit_code=EXIT_CONFLICT)
            destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            os.replace(source, destination)
        else:
            if source_digest == before and destination_digest is None:
                _atomic_write(destination, materials[material]["content"])
                destination_digest = after
            if source_digest == before and destination_digest == after:
                os.unlink(source)
            else:
                raise ContextError("precondition_changed", "changed-move state is invalid", exit_code=EXIT_CONFLICT)
        changed.extend([operation["from_path"], operation["to_path"]])
    elif op == "file_delete":
        path = _ensure_contained(repo, operation["path"])
        current = _digest_or_none(path)
        if current is None:
            return
        inbound = _inbound_refs(repo, operation["id"], path)
        target_id = parse_document(path.read_text(encoding="utf-8"), descriptor).frontmatter["id"] if current == operation["before_sha256"] else None
        if current != operation["before_sha256"] or target_id != operation["id"] or operation.get("inbound_refs") or inbound:
            raise ContextError("precondition_changed", "delete precondition changed", exit_code=EXIT_CONFLICT)
        os.unlink(path)
        changed.append(operation["path"])


def _apply_index_operation(repo: pathlib.Path, plan: dict[str, Any], operation: dict[str, Any], materials: dict[str, dict[str, Any]], changed: list[str], regenerated: list[str] | None = None) -> list[str]:
    transition = plan["transition"]
    index_paths = (
        _core_init_paths(operation["after_sha256"])
        if transition == "core_init"
        else sorted(operation["after_sha256"])
    )
    if transition in {"core_init", "area_register"}:
        by_path = {material["path"]: material for material in materials.values() if material.get("path")}
        pending: list[tuple[str, pathlib.Path]] = []
        for relative in index_paths:
            path = _ensure_contained(repo, relative)
            if path.exists() and not path.is_file():
                raise ContextError("precondition_changed", "index path is not a regular file", {"path": relative}, EXIT_CONFLICT)
            if path.parent.exists() and not path.parent.is_dir():
                raise ContextError("precondition_changed", "index parent is not a directory", {"path": relative}, EXIT_CONFLICT)
            current = _digest_or_none(path)
            if current == operation["after_sha256"][relative]:
                continue
            if current != operation["before_sha256"][relative] or relative not in by_path:
                raise ContextError("precondition_changed", "index precondition changed", {"path": relative}, EXIT_CONFLICT)
            pending.append((relative, path))
        if transition == "area_register":
            area = plan["owner_descriptor"]["kind"]
            area_root = _ensure_contained(repo, f"context/{area}")
            allowed_entry = f"{area}.index.md"
            if area_root.exists() and (
                not area_root.is_dir()
                or any(entry.name != allowed_entry for entry in area_root.iterdir())
            ):
                raise ContextError(
                    "precondition_changed",
                    "area register target contains content beyond its approved index seed",
                    {"area": area, "path": f"context/{area}"},
                    EXIT_CONFLICT,
                )
        if transition == "core_init":
            retired = _ensure_contained(repo, "context/observation/retired")
            if retired.exists() and (not retired.is_dir() or retired.is_symlink()):
                raise ContextError("precondition_changed", "observation retired path is not a safe directory", exit_code=EXIT_CONFLICT)
            retired.mkdir(mode=0o755, parents=True, exist_ok=True)
        for relative, path in pending:
            _atomic_write(path, by_path[relative]["content"])
            changed.append(relative)
    else:
        by_path = {
            material.get("path"): material
            for material in materials.values()
            if material.get("path")
        }
        for area in operation["areas"]:
            relative = f"context/{area}/{area}.index.md"
            path = repo / relative
            current = _digest_or_none(path)
            expected_before = operation["before_sha256"].get(relative)
            if current == operation["after_sha256"].get(relative):
                continue
            drifted = current != expected_before
            if drifted and (plan.get("source_type") != "owner_result" or plan.get("prior_same_area_bundle_digests")):
                raise ContextError("precondition_changed", "area index precondition changed", {"path": relative}, EXIT_CONFLICT)
            material = by_path.get(relative)
            if drifted or plan.get("source_type") != "owner_result" or material is None:
                # The projection changed underneath the approved plan (or was never bound):
                # re-derive it from the artifacts now on disk, inside the root lock. The
                # benign-drift guard in _validate_bundle already refused semantic conflicts.
                rendered = render_area_index_from_repository(repo, area, repair_rows=True)
            else:
                rendered = material["content"]
            rendered_digest = sha256_bytes(file_bytes(rendered))
            if not drifted and rendered_digest != operation["after_sha256"][relative]:
                raise ContextError("plan_preview_mismatch", "deterministic index output differs from preview", {"path": relative}, EXIT_INTEGRITY)
            if drifted and regenerated is not None:
                regenerated.append(relative)
            if rendered_digest == current:
                continue
            _atomic_write(path, rendered)
            changed.append(relative)
    return index_paths


def apply_bundle(repo: pathlib.Path, bundle: dict[str, Any], approved_digest: str, *, approval_source: str = "user", policy_decision: str | None = None, policy_reason: str | None = None) -> dict[str, Any]:
    if approval_source not in {"user", "explicit_init", "policy"}:
        raise ContextError("approval_required", "autonomous audit or maintenance cannot apply a durable mutation", exit_code=EXIT_CONFLICT)
    plan, materials = _validate_bundle(repo, bundle, approved_digest)
    if approval_source == "explicit_init" and plan["transition"] not in {"core_init", "area_register", *MANAGED_ROOT_TRANSITIONS}:
        raise ContextError(
            "approval_required",
            "explicit init authorizes only fixed core_init, area_register, and policy_install transitions",
            exit_code=EXIT_CONFLICT,
        )
    changed: list[str] = []
    index_paths: list[str] = []
    regenerated: list[str] = []
    project = project_settings(repo)["project"]
    policy_lock = _root_lock(project / ".bobbin") if plan.get("source_type") == "owner_result" else contextlib.nullcontext()
    with policy_lock, _root_lock(repo):
        plan, materials = _validate_bundle(repo, bundle, approved_digest)
        authorization = _config_call("authorize", sys.modules[__name__], repo, bundle, approval_source, policy_decision, policy_reason)
        plan_descriptor = plan.get("owner_descriptor")
        profiled_descriptor = (
            plan_descriptor
            if isinstance(plan_descriptor, dict) and plan_descriptor.get("schema") == "context-owner-descriptor/v2"
            else None
        )
        # Owner writes roll back as a unit; core_init/area_register/policy keep their
        # resumable exact-prefix contract (a retried explicit init completes them).
        journal = _journal_paths(repo, _plan_touched_paths(plan)) if plan.get("source_type") == "owner_result" else {}
        try:
            for operation in plan["operations"]:
                if operation["op"] == "index_rebuild":
                    index_paths = _apply_index_operation(repo, plan, operation, materials, changed, regenerated)
                else:
                    operation_descriptor = (
                        profiled_descriptor
                        if profiled_descriptor is not None and operation.get("area") == profiled_descriptor["kind"]
                        else None
                    )
                    _apply_file_operation(repo, operation, materials, changed, operation_descriptor)
        except BaseException:
            # No partial owner write survives: artifacts and indexes return to their pre-apply bytes.
            if journal:
                _restore_journal(repo, journal)
            raise
    warnings = [f"index_regenerated:{relative}" for relative in regenerated]
    return {"applied": True, "plan_id": plan["plan_id"], "approval_digest": approved_digest, "changed_paths": sorted(set(changed)), "index_paths": index_paths, "warnings": warnings, "authorization": authorization}


def _read_frozen_receipt(
    repo: pathlib.Path,
    receipt_file: str | pathlib.Path,
) -> tuple[pathlib.Path, dict[str, Any], tuple[int, int]]:
    path = _explicit_receipt_path(repo, receipt_file)
    parent_fd: int | None = None
    try:
        parent_fd = _open_receipt_parent_fd(path)
        metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ContextError(
                "receipt_unsafe",
                "receipt must be a private regular file without aliases",
                {"path": str(path)},
                EXIT_CONFLICT,
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path.name, flags, dir_fd=parent_fd)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ContextError("receipt_changed", "receipt changed while opening", exit_code=EXIT_CONFLICT)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(fd)
        text = b"".join(chunks).decode("utf-8")
    except ContextError:
        raise
    except (OSError, UnicodeError) as error:
        raise ContextError(
            "receipt_unavailable",
            "frozen receipt could not be read",
            {"path": str(path)},
            EXIT_NOT_FOUND,
        ) from error
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
    receipt = _strict_json_loads(text, code="receipt_invalid")
    if not isinstance(receipt, dict) or tuple(sorted(receipt)) != tuple(sorted(CORE_RECEIPT_FIELDS)):
        raise ContextError("receipt_invalid", "receipt fields differ from the exact core workflow contract", exit_code=EXIT_CONFLICT)
    if receipt.get("schema") != CORE_RECEIPT_SCHEMA or receipt.get("status") != "pending":
        raise ContextError("receipt_invalid", "receipt schema or status is invalid", exit_code=EXIT_CONFLICT)
    _validate_timestamp(receipt.get("created_at"), "created_at")
    plan_id = receipt.get("plan_id")
    if not _valid_plan_id(plan_id):
        raise ContextError("receipt_invalid", "receipt plan id is invalid", exit_code=EXIT_CONFLICT)
    core = receipt.get("core")
    if (
        not isinstance(core, dict)
        or set(core) != {"path", "sha256"}
        or not isinstance(core.get("path"), str)
        or not pathlib.Path(core["path"]).is_absolute()
        or not isinstance(core.get("sha256"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", core["sha256"])
    ):
        raise ContextError("receipt_invalid", "receipt core identity is invalid", exit_code=EXIT_CONFLICT)
    bundle = receipt.get("plan_bundle")
    nested_plan_id = (
        bundle.get("approval_material", {}).get("plan", {}).get("plan_id")
        if isinstance(bundle, dict)
        else None
    )
    if nested_plan_id != plan_id:
        raise ContextError("receipt_invalid", "receipt plan id differs from its exact plan bundle", exit_code=EXIT_CONFLICT)
    receipt_digest = receipt.get("receipt_digest")
    receipt_body = dict(receipt)
    receipt_body.pop("receipt_digest")
    if (
        not isinstance(receipt_digest, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt_digest)
        or receipt_digest != canonical_digest(receipt_body)
    ):
        raise ContextError("receipt_digest_mismatch", "receipt damage digest does not match", exit_code=EXIT_CONFLICT)
    return path, receipt, (metadata.st_dev, metadata.st_ino)


def _receipt_plan_is_fully_applied(repo: pathlib.Path, plan: dict[str, Any]) -> bool:
    operations = plan.get("operations", [])
    if not isinstance(operations, list) or not operations:
        return False
    for operation in operations:
        op = operation.get("op")
        if op == "index_rebuild":
            after = operation.get("after_sha256")
            if not isinstance(after, dict) or any(
                _digest_or_none(_ensure_contained(repo, path)) != digest
                for path, digest in after.items()
            ):
                return False
        elif op in {"file_create", "file_replace"}:
            if _digest_or_none(_ensure_contained(repo, operation.get("path", ""))) != operation.get("after_sha256"):
                return False
        elif op == "file_move":
            source = _ensure_contained(repo, operation.get("from_path", ""))
            destination = _ensure_contained(repo, operation.get("to_path", ""))
            if source.exists() or _digest_or_none(destination) != operation.get("after_sha256"):
                return False
        elif op == "file_delete":
            if _ensure_contained(repo, operation.get("path", "")).exists():
                return False
        else:
            return False
    return True


def _unlink_receipt(path: pathlib.Path, identity: tuple[int, int]) -> None:
    parent_fd = _open_receipt_parent_fd(path)
    try:
        metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != identity
        ):
            raise OSError("receipt path identity changed")
        os.unlink(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def apply_receipt(
    repo: pathlib.Path,
    receipt_file: str | pathlib.Path,
    approved_digest: str,
    *, approval_source: str = "user", policy_decision: str | None = None, policy_reason: str | None = None,
) -> dict[str, Any]:
    path, receipt, receipt_identity = _read_frozen_receipt(repo, receipt_file)
    core = receipt["core"]
    bundle = receipt["plan_bundle"]
    workflow_digest = _workflow_approval_digest(core, bundle)
    if approved_digest != workflow_digest:
        raise ContextError(
            "workflow_approval_digest_mismatch",
            "approved workflow digest does not match the frozen core and plan bundle",
            exit_code=EXIT_CONFLICT,
        )
    if core != _core_identity():
        raise ContextError("core_surface_mismatch", "frozen receipt belongs to a different core entrypoint", exit_code=EXIT_CONFLICT)
    nested_digest = bundle.get("approval_digest") if isinstance(bundle, dict) else None
    if not isinstance(nested_digest, str):
        raise ContextError("bundle_invalid", "receipt mutation bundle is invalid", exit_code=EXIT_CONFLICT)
    plan, _ = _validate_bundle(repo, bundle, nested_digest)
    if _receipt_plan_is_fully_applied(repo, plan):
        raise ContextError("receipt_already_applied", "receipt plan is already fully applied", exit_code=EXIT_CONFLICT)
    result = apply_bundle(repo, bundle, nested_digest, approval_source=approval_source,
                          policy_decision=policy_decision, policy_reason=policy_reason)
    result["approval_digest"] = approved_digest
    try:
        _unlink_receipt(path, receipt_identity)
    except OSError:
        result["warnings"] = [*result.get("warnings", []), "receipt_cleanup_failed"]
    return result


def refresh_repository(repo: pathlib.Path) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    root_text = ""
    root_valid = False
    try:
        root_path = _ensure_contained(repo, ROOT_INDEX)
        root_text = root_path.read_text(encoding="utf-8")
        _, areas = parse_root_index(root_text)
        root_valid = True
    except FileNotFoundError:
        issues.append({"code": "index_missing", "path": ROOT_INDEX})
        areas = []
    except (OSError, UnicodeError) as error:
        issues.append({"code": "index_invalid", "path": ROOT_INDEX, "message": str(error)})
        areas = []
    except ContextError as error:
        issues.append({"code": error.code, "path": error.details.get("path", ROOT_INDEX), "message": error.message})
        areas = []
    if not root_valid:
        return {
            "schema": "context-integrity-result/v1",
            "ok": not issues,
            "issues": sorted(issues, key=canonical_json),
            "warnings": sorted(warnings, key=canonical_json),
            "root_digest": sha256_bytes(root_text.encode("utf-8")),
        }
    area_names = [area["area"] for area in areas]
    claims = [claim for area in areas for claim in area["claims"]]
    for area in sorted(set(BUILTIN_AREAS) - set(area_names)):
        warnings.append({"code": "reserved_index_missing", "path": f"context/{area}/{area}.index.md", "area": area})
    if len(area_names) != len(set(area_names)):
        issues.append({"code": "duplicate_area_owner", "path": ROOT_INDEX})
    if len(claims) != len(set(claims)):
        issues.append({"code": "duplicate_claim_owner", "path": ROOT_INDEX})
    seen_ids: dict[str, str] = {}
    documents: dict[str, tuple[str, dict[str, Any]]] = {}
    document_kinds: dict[str, str] = {}
    observation_evidence_refs: dict[str, list[str]] = {}
    root_specs: list[tuple[dict[str, Any], str, str]] = []
    for area in areas:
        index_valid = True
        index_text: str | None = None
        descriptor: dict[str, Any] | None = None
        try:
            path = _ensure_contained(repo, area["path"])
            index_text = path.read_text(encoding="utf-8")
            index = parse_area_index(index_text)
            try:
                descriptor = _descriptor_for_area_bytes(root_text, area, index_text)
            except ContextError as error:
                issues.append({
                    "code": "owner_profile_mismatch",
                    "path": area["path"],
                    "area": area["area"],
                    "cause": error.code,
                })
                continue
        except FileNotFoundError:
            issues.append({"code": "index_missing", "path": area["path"]})
            continue
        except (OSError, UnicodeError) as error:
            issues.append({"code": "index_invalid", "path": area["path"], "message": str(error)})
            continue
        except ContextError as error:
            if index_text is None:
                issues.append({"code": error.code, "path": error.details.get("path", area["path"]), "message": error.message})
                continue
            try:
                metadata = _parse_area_index_metadata(index_text)
            except ContextError:
                issues.append({"code": error.code, "path": error.details.get("path", area["path"]), "message": error.message})
                continue
            warning_code = error.code if error.code.startswith("index_") else "index_content_drift"
            warning = {"code": warning_code, "path": area["path"]}
            if warning_code != error.code:
                warning["cause"] = error.code
            warnings.append(warning)
            index = AreaIndex(frontmatter=metadata, current=[], history=[], text=index_text)
            index_valid = False
        metadata = index.frontmatter
        root_specs.append((area, _area_label(area["area"]), metadata["summary"]))
        if (
            metadata["area"],
            metadata["owner"],
            metadata["artifact_schema"],
            metadata["authority"],
        ) != (
            area["area"],
            area["owner"],
            area["artifact_schema"],
            area["authority"],
        ):
            warnings.append({"code": "area_index_mismatch", "path": area["path"]})
        actual: dict[str, dict[str, Any]] = {}
        try:
            for artifact_path, state in _scan_area_paths(repo, area["area"]):
                relative = artifact_path.relative_to(repo).as_posix()
                try:
                    row = _entry_from_document(repo, artifact_path, metadata, state, descriptor=descriptor)
                    document = parse_document(artifact_path.read_text(encoding="utf-8"), descriptor)
                    _validate_strict_lifecycle(document.frontmatter, state, relative, descriptor)
                    warnings.extend(
                        {**warning, "path": relative}
                        for warning in document.warnings
                    )
                except (OSError, UnicodeError) as error:
                    issues.append({"code": "artifact_invalid", "path": relative, "message": str(error)})
                    continue
                except ContextError as error:
                    issues.append({"code": error.code, "path": relative})
                    continue
                if row["id"] in seen_ids:
                    issues.append({"code": "duplicate_id", "path": relative, "other": seen_ids[row["id"]]})
                else:
                    seen_ids[row["id"]] = relative
                    documents[row["id"]] = (relative, document.frontmatter)
                    document_kinds[row["id"]] = area["area"]
                    if document.frontmatter.get("schema") == "context-observation/v1":
                        evidence = _section_value(document.sections, "context-observation/v1", "Evidence")
                        observation_evidence_refs[row["id"]] = [
                            item for item in _list_section_items(evidence) if is_context_id(item)
                        ]
                actual[row["id"]] = row
        except ContextError as error:
            issues.append({"code": error.code, "path": error.details.get("path", f"context/{area['area']}")})
        projected = {row["id"]: row for row in index.current + index.history} if index_valid else {}
        for identifier in sorted(set(projected) - set(actual)):
            warnings.append({"code": "index_ghost_entry", "path": projected[identifier]["path"], "id": identifier})
        for identifier in sorted(set(actual) - set(projected)):
            warnings.append({"code": "index_missing_entry", "path": actual[identifier]["path"], "id": identifier})
        for identifier in sorted(set(actual) & set(projected)):
            if actual[identifier] != projected[identifier]:
                code = "index_ghost_entry" if actual[identifier]["path"] != projected[identifier]["path"] else "index_content_drift"
                warnings.append({"code": code, "path": projected[identifier]["path"], "actual_path": actual[identifier]["path"], "id": identifier})
                if code == "index_ghost_entry":
                    warnings.append({"code": "index_missing_entry", "path": actual[identifier]["path"], "id": identifier})
        try:
            regenerated = render_area_index_from_repository(repo, area["area"])
            if file_bytes(regenerated) != path.read_bytes() and not any(
                issue.get("code") in INDEX_FIXABLE_CODES
                and issue.get("path", "").startswith(f"context/{area['area']}/")
                for issue in warnings
            ):
                warnings.append({"code": "index_content_drift", "path": area["path"]})
        except ContextError:
            pass
    if len(root_specs) == len(areas) and len(area_names) == len(set(area_names)):
        try:
            regenerated_root = render_root_index(root_text, root_specs)
            if file_bytes(regenerated_root) != (repo / ROOT_INDEX).read_bytes():
                warnings.append({"code": "root_index_drift", "path": ROOT_INDEX})
        except (ContextError, OSError):
            pass
    for identifier, (path, frontmatter) in documents.items():
        refs: list[str] = []
        for key in ("anchors", "supersedes", "superseded_by"):
            value = frontmatter.get(key, [])
            refs.extend(value if isinstance(value, list) else [value])
        relations = frontmatter.get("relations", {})
        if isinstance(relations, dict):
            for value in relations.values():
                refs.extend(value if isinstance(value, list) else [value])
        refs.extend(observation_evidence_refs.get(identifier, []))
        try:
            typed_relations = _typed_relation_entries(frontmatter)
        except ContextError as error:
            issues.append({"code": error.code, "path": path, "id": identifier})
            typed_relations = []
        for relation, expected_kind, target in typed_relations:
            actual_kind = document_kinds.get(target)
            if actual_kind is not None and actual_kind != expected_kind:
                issues.append({
                    "code": "typed_relation_kind_mismatch",
                    "path": path,
                    "id": identifier,
                    "relation": relation,
                    "target": target,
                    "expected_kind": expected_kind,
                    "actual_kind": actual_kind,
                })
        for target in refs:
            if isinstance(target, str) and target.startswith("ctx_") and target not in documents:
                issues.append({"code": "broken_internal_ref", "path": path, "id": identifier, "target": target})
        retired = "/retired/" in path
        reason = frontmatter.get("retired_reason")
        if retired and reason == "superseded":
            successor_id = frontmatter.get("superseded_by")
            successor = documents.get(successor_id)
            if successor is not None and identifier not in successor[1].get("supersedes", []):
                issues.append({"code": "supersede_edge_missing", "path": path, "id": identifier, "target": successor_id})
        if retired and reason == "invalidated" and "superseded_by" in frontmatter:
            issues.append({"code": "lifecycle_invalid", "path": path, "id": identifier})
        if not retired and ("retired_at" in frontmatter or reason is not None or "superseded_by" in frontmatter):
            issues.append({"code": "lifecycle_invalid", "path": path, "id": identifier})
        for predecessor_id in frontmatter.get("supersedes", []):
            predecessor = documents.get(predecessor_id)
            if predecessor is not None and predecessor[1].get("superseded_by") != identifier:
                issues.append({"code": "supersede_edge_missing", "path": path, "id": identifier, "target": predecessor_id})
            if predecessor is not None and predecessor[1].get("schema") != frontmatter.get("schema"):
                allowed_fallback = (
                    predecessor[1].get("schema") == "context-observation/v1"
                    and frontmatter.get("schema") == "context-decision/v1"
                    and predecessor[1].get("kind_hint") == "decision"
                )
                if not allowed_fallback:
                    issues.append({"code": "illegal_cross_kind_predecessor", "path": path, "id": identifier, "target": predecessor_id})
    current_documents = {
        identifier: frontmatter
        for identifier, (path, frontmatter) in documents.items()
        if document_kinds.get(identifier) == "document" and "/retired/" not in path
    }
    stale_documents: dict[str, list[str]] = {}
    for decision_id, (path, frontmatter) in documents.items():
        if document_kinds.get(decision_id) != "decision" or "/retired/" in path:
            continue
        relations = frontmatter.get("relations", {})
        affected = relations.get("affects:document", []) if isinstance(relations, dict) else []
        if not isinstance(affected, list):
            continue
        decision_at = datetime.datetime.fromisoformat(frontmatter["created_at"])
        for document_id in affected:
            target = current_documents.get(document_id)
            if target is None:
                continue
            document_at = datetime.datetime.fromisoformat(target.get("updated_at", target["created_at"]))
            if decision_at > document_at:
                stale_documents.setdefault(document_id, []).append(decision_id)
    warnings.extend(
        {
            "check": "document-stale-vs-decision",
            "tier": "hygiene-warn",
            "document": document_id,
            "newer_decisions": sorted(decision_ids),
        }
        for document_id, decision_ids in sorted(stale_documents.items())
    )
    successor_edges = {
        identifier: frontmatter["superseded_by"]
        for identifier, (_, frontmatter) in documents.items()
        if frontmatter.get("retired_reason") == "superseded" and isinstance(frontmatter.get("superseded_by"), str)
    }
    states: dict[str, int] = {}

    def visit(identifier: str, trail: list[str]) -> None:
        state = states.get(identifier, 0)
        if state == 1:
            start = trail.index(identifier) if identifier in trail else 0
            cycle = trail[start:] + [identifier]
            issues.append({"code": "lifecycle_cycle", "path": documents[identifier][0], "ids": cycle})
            return
        if state == 2:
            return
        states[identifier] = 1
        successor = successor_edges.get(identifier)
        if successor in documents:
            visit(successor, [*trail, identifier])
        states[identifier] = 2

    for identifier in sorted(successor_edges):
        if states.get(identifier, 0) == 0:
            visit(identifier, [])
    current_slots: dict[tuple[str, str], str] = {}
    for identifier, (path, frontmatter) in documents.items():
        if frontmatter.get("schema") == "context-decision/v1" and "/retired/" not in path:
            slot = (
                _canonical_decision_scope(frontmatter.get("scope", "")),
                _canonical_decision_key(frontmatter.get("decision_key", "")),
            )
            if slot in current_slots:
                issues.append({"code": "duplicate_current_slot", "path": path, "other": current_slots[slot]})
            current_slots[slot] = path
    return {
        "schema": "context-integrity-result/v1",
        "ok": not issues,
        "issues": sorted(issues, key=canonical_json),
        "warnings": sorted(warnings, key=canonical_json),
        "root_digest": sha256_bytes(root_text.encode("utf-8")),
    }


def _doctor_self_report() -> dict[str, str]:
    entrypoint = pathlib.Path(__file__).resolve(strict=True)
    manifest_path = entrypoint.parents[3] / ".claude-plugin/plugin.json"
    try:
        manifest = _strict_json_loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise ContextError("plugin_manifest_invalid", "context-core plugin manifest is unavailable", exit_code=EXIT_CONFLICT) from error
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("name") != "bobbin"
        or not isinstance(version, str)
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
    ):
        raise ContextError("plugin_manifest_invalid", "context-core plugin identity is invalid", exit_code=EXIT_CONFLICT)
    return {"plugin_version": version, "entrypoint": str(entrypoint), "protocol": PROTOCOL}


def _cached_core_release_warning(entrypoint: pathlib.Path) -> list[dict[str, Any]]:
    """Compare the loaded catalog pin with exact sibling cache versions, without executing one."""

    try:
        resolved = entrypoint.resolve(strict=True)
        plugin_root = resolved.parents[3]
        container = plugin_root.parent if plugin_root.parent.name == "bobbin" else plugin_root
        roots = [plugin_root]
        if container != plugin_root:
            metadata = os.lstat(container)
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                roots.extend(
                    child
                    for child in container.iterdir()
                    if not child.is_symlink() and child.is_dir()
                )
        candidates: list[tuple[tuple[int, int, int], str, pathlib.Path]] = []
        for root in roots:
            try:
                versions: set[str] = set()
                for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
                    manifest_path = root / relative
                    metadata = os.lstat(manifest_path)
                    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                        raise OSError("unsafe manifest")
                    manifest = _strict_json_loads(manifest_path.read_text(encoding="utf-8"))
                    if not isinstance(manifest, dict) or manifest.get("name") != "bobbin":
                        raise OSError("invalid manifest identity")
                    version = manifest.get("version")
                    if not isinstance(version, str):
                        raise OSError("invalid manifest version")
                    versions.add(version)
                if len(versions) != 1:
                    continue
                version = versions.pop()
                match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version)
                candidate_entrypoint = root / "skills/context/scripts/context_cli.py"
                if match is None or candidate_entrypoint.is_symlink() or not candidate_entrypoint.is_file():
                    continue
                candidates.append((tuple(int(part) for part in match.groups()), version, candidate_entrypoint.resolve(strict=True)))
            except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError, ContextError):
                continue
        loaded = next((item for item in candidates if item[2] == resolved), None)
        if loaded is None or not candidates:
            return []
        compatible = [item for item in candidates if item[0][0] == loaded[0][0]]
        latest = max(compatible, key=lambda item: (item[0], str(item[2])))
        if latest[0] <= loaded[0]:
            return []
        return [{
            "code": "catalog_pin_behind_cache",
            "plugin": "bobbin",
            "catalog_version": loaded[1],
            "cache_latest_version": latest[1],
            "compatible_candidate": str(latest[2]),
            "compatibility_basis": "same-major manifests and public entrypoint layout; runtime handshake required",
            "candidate_policy": "diagnostic_only_no_automatic_substitution",
        }]
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError, ContextError):
        return []


def _doctor_result(
    repository_state: str,
    *,
    issues: list[dict[str, Any]] | None = None,
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    release_warnings = _cached_core_release_warning(pathlib.Path(__file__))
    return {
        "schema": "context-core-doctor/v1",
        "owner": "context-core",
        "supported_protocols": [PROTOCOL],
        "repository_state": repository_state,
        "root": "context/",
        "issues": issues or [],
        "warnings": [*(warnings or []), *release_warnings],
        **_doctor_self_report(),
    }


def doctor_repository(repo: pathlib.Path) -> dict[str, Any]:
    root = repo / "context"
    root_index = repo / ROOT_INDEX
    if not root.exists():
        return _doctor_result("absent")
    if not root_index.exists():
        return _doctor_result("partial", warnings=[{"code": "index_missing", "path": ROOT_INDEX}])
    try:
        result = refresh_repository(repo)
    except ContextError as error:
        return _doctor_result("partial", issues=[{"code": error.code, "path": error.details.get("path")}])
    hygiene: list[dict[str, Any]] = []
    if _git_metadata_root(repo) is not None and not _merge_attributes_installed(repo):
        hygiene.append({
            "code": "merge_attributes_missing",
            "path": MERGE_ATTRIBUTES_TARGET,
            "hint": "run init for your host again, or add the managed context-core-merge block so generated indexes union-merge and `refresh --fix index` re-derives them",
        })
    return _doctor_result(
        "ready" if result["ok"] else "invalid",
        issues=result["issues"],
        warnings=[*result["warnings"], *hygiene],
    )


def schema_result() -> dict[str, Any]:
    return {
        "schema": "context-core-schema/v1", "protocol": PROTOCOL, "storage_root": "context/", "root_override": True,
        "features": [
            "context-owner-descriptor/v2", "filesystem-vault/v1",
            "typed-relations/v1",
            "owner-inline-workflow/v1",
            "context-archive/v1",
            "bobbin-project/v1", "recording-policy/v1",
        ],
        "id": "ctx_<lowercase-uuidv4-hex>", "json_success": {"ok": True, "result": {}},
        "preview_success": {
            "ok": True,
            "applied": False,
            "state": "awaiting_approval",
            "result": {"applied": False, "state": "awaiting_approval"},
        },
        "json_error": {"ok": False, "error": {"code": "string", "message": "string", "details": {}}},
        "exit_codes": {"usage_schema_filename": 2, "not_found": 3, "conflict": 5, "integrity_index": 6},
        "commands": [
            "schema", "capabilities", "settings", "doctor", "init", "bootstrap", "draft", "lifecycle prepare", "area register",
            "transaction preview", "transaction apply", "recall", "snapshot save/update/list/search/load/discard",
            "observation preview/capture/read/search/annotate/reverify/invalidate/supersede/discard", "rename", "discard", "refresh",
            "archive preview/read/search/discard",
        ],
        "receipt": {
            "schema": CORE_RECEIPT_SCHEMA,
            "fields": list(CORE_RECEIPT_FIELDS),
            "workflow_approval_material": ["core", "plan_bundle"],
            "receipt_digest_role": "damage_detection_only",
            "selection": "agent_retained_explicit_path",
        },
        "project_configuration": {
            "schema": "bobbin-project/v1", "path": ".bobbin/config.json",
            "approval_modes": ["explicit", "auto", "adaptive"], "default_approval_mode": "explicit",
            "approval_sources": ["user", "policy"], "adaptive_assessment": ["record", "ask"],
            "semantic_attestations_required": True, "pending_policy_binding": True,
        },
    }


def _vault_root(vault: str | None = None) -> pathlib.Path:
    try:
        if vault is not None:
            root = pathlib.Path(vault).expanduser().resolve(strict=True)
            if not root.is_dir():
                raise OSError("vault root is not a directory")
            return root
        configured = _config_call("configured_vault")
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
        raise ContextError("vault_not_found", "vault must be an existing directory", exit_code=EXIT_NOT_FOUND) from error


def _read_input_file(value: str) -> str:
    try:
        return pathlib.Path(value).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ContextError(
            "input_unavailable",
            "input file is unavailable or not valid UTF-8",
            {"path": value},
            EXIT_NOT_FOUND,
        ) from error


def _load_json_argument(value: str, *, allow_stdin: bool = False) -> Any:
    if value == "@-":
        if not allow_stdin:
            raise ContextError("usage_invalid", "stdin is not supported for this argument")
        text = sys.stdin.read()
    elif value.startswith("@"):
        text = _read_input_file(value[1:])
    else:
        raise ContextError("usage_invalid", "JSON input must use @file or @-")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ContextError("schema_invalid", "input is not valid JSON") from error


def _load_descriptor_argument(value: str, *, allow_stdin: bool = False) -> dict[str, Any]:
    if value == "@-":
        if not allow_stdin:
            raise ContextError("usage_invalid", "stdin is not supported for this argument")
        text = sys.stdin.read()
    elif value.startswith("@"):
        text = _read_input_file(value[1:])
    else:
        raise ContextError("usage_invalid", "JSON input must use @file or @-")
    descriptor = _strict_json_loads(text, code="owner_descriptor_invalid")
    validate_owner_descriptor(descriptor)
    if descriptor.get("schema") == "context-owner-descriptor/v2":
        canonical = canonical_json(descriptor)
        if text not in {canonical, canonical + "\n"}:
            raise ContextError(
                "owner_descriptor_invalid",
                "v2 owner descriptor input must be canonical JSON",
                exit_code=EXIT_CONFLICT,
            )
        if len(canonical.encode("utf-8")) > MAX_OWNER_DESCRIPTOR_BYTES:
            raise ContextError("owner_descriptor_invalid", "owner descriptor exceeds 8 KiB", exit_code=EXIT_CONFLICT)
    return descriptor


def _load_text_argument(value: str) -> str:
    if not value.startswith("@") or value == "@-":
        raise ContextError("usage_invalid", "text input must use @file")
    return _read_input_file(value[1:])


def _read_body_file(path: pathlib.Path, maximum_bytes: int = MAX_OWNER_INPUT_BYTES) -> str:
    if path.is_symlink():
        raise ContextError(
            "input_unavailable",
            "body input must be a regular non-symlink UTF-8 file",
            {"path": str(path), "reason": "symlink"},
            EXIT_NOT_FOUND,
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ContextError(
                "input_unavailable",
                "body input must be a regular non-symlink UTF-8 file",
                {"path": str(path), "reason": "not_regular"},
                EXIT_NOT_FOUND,
            )
        if metadata.st_size > maximum_bytes:
            raise ContextError(
                "input_too_large",
                "body input file exceeds its byte budget",
                {"path": str(path), **_byte_size_details(metadata.st_size, maximum_bytes)},
                EXIT_CONFLICT,
            )
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum_bytes:
            raise ContextError(
                "input_too_large",
                "body input file exceeds its byte budget",
                {"path": str(path), **_byte_size_details(len(payload), maximum_bytes)},
                EXIT_CONFLICT,
            )
        return payload.decode("utf-8")
    except ContextError:
        raise
    except (OSError, UnicodeError) as error:
        raise ContextError(
            "input_unavailable",
            "body input must be a regular non-symlink UTF-8 file",
            {"path": str(path)},
            EXIT_NOT_FOUND,
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_body_argument(value: str, maximum_bytes: int = MAX_OWNER_INPUT_BYTES) -> str:
    if value.startswith("@@"):
        return value[1:]
    if value == "@-":
        return sys.stdin.read()
    if value.startswith("@"):
        return _read_body_file(pathlib.Path(value[1:]), maximum_bytes)
    return value


def _direct_attestation(value: dict[str, Any], candidate: dict[str, Any], kind: str) -> dict[str, Any]:
    normalized = dict(value)
    normalized.setdefault("schema", "context-semantic-attestation/v1")
    normalized.setdefault("operation", "claim")
    normalized.setdefault("input_schema", candidate["schema"])
    normalized.setdefault("input_digest", canonical_digest(candidate))
    _validate_attestation(normalized, "claim", candidate, set(builtin_capability(kind)["claim_assertions"]))
    return normalized


def _capture_attestation(args: argparse.Namespace, candidate: dict[str, Any], kind: str) -> tuple[dict[str, Any], bool]:
    flag_contract = {
        "observation": (
            ("attest_reusable_observation", "reusable_observation", "/owner_inputs/observation/observation"),
            ("attest_evidence_present", "evidence_present", "/owner_inputs/observation/evidence/0"),
        ),
        "snapshot": (
            ("attest_handoff_requested", "handoff_requested", "/owner_inputs/snapshot/current_context"),
            ("attest_unfinished_context_present", "unfinished_context_present", "/owner_inputs/snapshot/open_items/0"),
        ),
        "archive": (
            ("attest_source_adopted", "source_adopted_as_evidence", "/source_refs/0"),
            ("attest_immutable_original", "immutable_original_present", "/owner_inputs/archive/content"),
        ),
    }[kind]
    selected = [bool(getattr(args, argument)) for argument, _, _ in flag_contract]
    if args.attestation is not None and any(selected):
        raise ContextError("usage_invalid", "attestation file and semantic flags are mutually exclusive")
    if args.attestation is not None:
        return _direct_attestation(_load_json_argument(args.attestation), candidate, kind), False
    if not all(selected):
        raise ContextError("usage_invalid", "complete semantic attestation flags are required")
    value = {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": candidate["schema"],
        "input_digest": canonical_digest(candidate),
        "assertions": [
            {"name": name, "value": True, "evidence_pointers": [pointer]}
            for _, name, pointer in flag_contract
        ],
    }
    return _direct_attestation(value, candidate, kind), True


def _section_arguments(args: argparse.Namespace, mapping: Sequence[tuple[str, str]]) -> dict[str, str]:
    output: dict[str, str] = {}
    for argument, section in mapping:
        value = getattr(args, argument, None)
        if value is not None:
            output[section] = _load_body_argument(value)
    return output


_ITEM_MARKER = re.compile(r"^(?:[-*\u2022]\s+|\d{1,3}[.)]\s+)")


def _body_to_items(value: str) -> list[str]:
    """One item per non-empty line; a leading `- `, `* `, `•` or `1.` marker is optional.

    A single line is one item, so callers can pass a sentence or a bullet list without
    learning a format first.
    """
    body = _load_body_argument(value).strip()
    if not body:
        return []
    items = []
    for line in body.splitlines():
        stripped = _ITEM_MARKER.sub("", line.strip(), count=1).strip()
        if stripped:
            items.append(stripped)
    return items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="context_cli.py")
    parser.add_argument("--vault", help="Directory containing context/; defaults to the nearest context/ ancestor or cwd.")
    parser.add_argument("--project", help="Project settings root; independent of a shared --vault.")
    sub = parser.add_subparsers(dest="command", required=True)
    settings = sub.add_parser("settings", help="Read project configuration without changing it.")
    settings.add_argument("--json", action="store_true")
    for name in ("schema", "capabilities", "doctor"):
        command = sub.add_parser(name)
        command.add_argument("--json", action="store_true")
    init = sub.add_parser("init")
    init.add_argument("--host", choices=tuple(POLICY_HOST_TARGETS), required=True)
    init.add_argument("--json", action="store_true")
    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--descriptor", required=True)
    bootstrap.add_argument("--index-seed", required=True)
    bootstrap.add_argument("--host", choices=tuple(POLICY_HOST_TARGETS), required=True)
    bootstrap.add_argument("--json", action="store_true")
    draft = sub.add_parser("draft")
    draft.add_argument("--kind", choices=BUILTIN_AREAS, required=True)
    draft.add_argument("--candidate", required=True)
    draft.add_argument("--attestation", required=True)
    draft.add_argument("--json", action="store_true")
    lifecycle = sub.add_parser("lifecycle")
    lifecycle_sub = lifecycle.add_subparsers(dest="lifecycle_command", required=True)
    prepare = lifecycle_sub.add_parser("prepare")
    prepare.add_argument("--transition", choices=("observation_supersede", "decision_fallback_import"), required=True)
    prepare.add_argument("--predecessor", required=True)
    prepare.add_argument("--successor-result", required=True)
    prepare.add_argument("--json", action="store_true")
    area = sub.add_parser("area")
    area_sub = area.add_subparsers(dest="area_command", required=True)
    register = area_sub.add_parser("register")
    register.add_argument("--descriptor", required=True)
    register.add_argument("--index-seed", required=True)
    register.add_argument("--json", action="store_true")
    transaction = sub.add_parser("transaction")
    transaction_sub = transaction.add_subparsers(dest="transaction_command", required=True)
    preview = transaction_sub.add_parser("preview")
    preview.add_argument("--owner-result", required=True)
    preview.add_argument("--owner-validation")
    preview.add_argument("--prior-bundle", action="append", default=[])
    preview.add_argument("--json", action="store_true")
    apply = transaction_sub.add_parser("apply")
    apply_source = apply.add_mutually_exclusive_group(required=True)
    apply_source.add_argument("--plan-bundle")
    apply_source.add_argument("--receipt-file")
    apply.add_argument("--approved-digest", required=True)
    apply.add_argument("--approval-source", choices=("user", "policy"), default="user")
    apply.add_argument("--policy-decision", choices=("record", "ask"))
    apply.add_argument("--policy-reason")
    apply.add_argument("--json", action="store_true")
    candidate_parser = sub.add_parser("candidate")
    candidate_sub = candidate_parser.add_subparsers(dest="candidate_command", required=True)
    route = candidate_sub.add_parser("route")
    route.add_argument("--batch", required=True)
    route.add_argument("--capabilities", required=True)
    route.add_argument("--claim-results", required=True)
    route.add_argument("--json", action="store_true")
    policy = sub.add_parser("policy")
    policy_sub = policy.add_subparsers(dest="policy_command", required=True)
    policy_preview = policy_sub.add_parser("preview")
    policy_preview.add_argument("--target", choices=tuple(sorted(POLICY_TARGETS)), required=True)
    policy_preview.add_argument("--json", action="store_true")
    recall = sub.add_parser("recall")
    recall.add_argument("--query", default="")
    recall.add_argument("--area", action="append", default=[])
    recall.add_argument("--include-history", action="store_true")
    recall.add_argument("--include-archive", action="store_true")
    recall.add_argument("--facet", action="append", default=[])
    recall.add_argument("--limit", type=int, default=8)
    recall.add_argument("--pack", action="store_true")
    recall.add_argument("--section", action="append", default=[])
    recall.add_argument("--read", action="append", default=[])
    recall.add_argument("--strict-index", action="store_true")
    recall.add_argument("--max-bytes", type=int)
    recall.add_argument("--json", action="store_true")
    rename = sub.add_parser("rename")
    rename.add_argument("--id", required=True)
    rename.add_argument("--filename", required=True)
    rename.add_argument("--receipt-file")
    rename.add_argument("--json", action="store_true")
    discard = sub.add_parser("discard")
    discard.add_argument("--id", required=True)
    discard.add_argument("--receipt-file")
    discard.add_argument("--json", action="store_true")
    snapshot = sub.add_parser("snapshot")
    snapshot_sub = snapshot.add_subparsers(dest="snapshot_command", required=True)
    snapshot_save = snapshot_sub.add_parser("save")
    snapshot_save.add_argument("--title", required=True)
    snapshot_save.add_argument("--summary", required=True)
    snapshot_save.add_argument("--filename")
    snapshot_save.add_argument("--captured-from", choices=("conversation", "workspace", "manual", "import"), required=True)
    snapshot_save.add_argument("--attestation")
    snapshot_save.add_argument("--attest-handoff-requested", action="store_true", help="Attest a direct handoff request or the user's standing auto/adaptive project recording policy.")
    snapshot_save.add_argument("--attest-unfinished-context-present", action="store_true")
    snapshot_save.add_argument("--receipt-file")
    snapshot_save.add_argument("--approved", action="store_true", help="Attest the user's direct, explicit, unconditional handoff approval given in conversation; preview and apply in one call.")
    snapshot_save.add_argument("--sec-context", required=True)
    snapshot_save.add_argument("--sec-open-items", required=True)
    snapshot_save.add_argument("--sec-next-steps", required=True)
    snapshot_save.add_argument("--sec-decided")
    snapshot_save.add_argument("--sec-refs")
    snapshot_save.add_argument("--sec-candidates")
    snapshot_save.add_argument("--anchor", action="append", default=[])
    snapshot_save.add_argument("--source-ref", action="append", default=[])
    snapshot_save.add_argument("--tag", action="append", default=[])
    snapshot_save.add_argument("--search-term", action="append", default=[])
    snapshot_save.add_argument("--json", action="store_true")
    snapshot_update = snapshot_sub.add_parser("update")
    snapshot_update.add_argument("--id", required=True)
    snapshot_update.add_argument("--title")
    snapshot_update.add_argument("--summary")
    snapshot_update.add_argument("--merge", action="store_true")
    snapshot_update.add_argument("--sec-context")
    snapshot_update.add_argument("--sec-open-items")
    snapshot_update.add_argument("--sec-next-steps")
    snapshot_update.add_argument("--sec-decided")
    snapshot_update.add_argument("--sec-refs")
    snapshot_update.add_argument("--sec-candidates")
    snapshot_update.add_argument("--anchor", action="append")
    snapshot_update.add_argument("--source-ref", action="append")
    snapshot_update.add_argument("--tag", action="append")
    snapshot_update.add_argument("--search-term", action="append")
    snapshot_update.add_argument("--clear", action="append", default=[])
    snapshot_update.add_argument("--receipt-file")
    snapshot_update.add_argument("--approved", action="store_true", help="Attest the user's direct, explicit, unconditional approval given in conversation; preview and apply in one call.")
    snapshot_update.add_argument("--json", action="store_true")
    for name in ("list", "search", "load", "discard"):
        command = snapshot_sub.add_parser(name)
        if name in {"load", "discard"}:
            command.add_argument("--id", required=True)
        if name == "discard":
            command.add_argument("--receipt-file")
        if name == "search":
            command.add_argument("--query", required=True)
        if name in {"list", "search"}:
            command.add_argument("--limit", type=int, default=8)
        if name == "load":
            command.add_argument("--section", action="append", default=[])
            command.add_argument("--max-bytes", type=int)
        command.add_argument("--json", action="store_true")
    observation = sub.add_parser("observation")
    observation_sub = observation.add_subparsers(dest="observation_command", required=True)
    for command_name, command_help in (
        ("preview", "Build and freeze an OBS approval preview without recording it."),
        ("capture", "Deprecated alias for 'observation preview'; does not record the OBS."),
    ):
        observation_preview = observation_sub.add_parser(
            command_name,
            help=command_help,
            description=command_help,
        )
        observation_preview.add_argument("--title", required=True)
        observation_preview.add_argument("--summary", required=True)
        observation_preview.add_argument("--filename")
        observation_preview.add_argument("--captured-from", choices=("conversation", "workspace", "manual", "import"), required=True)
        observation_preview.add_argument("--attestation")
        observation_preview.add_argument("--attest-reusable-observation", action="store_true")
        observation_preview.add_argument("--attest-evidence-present", action="store_true")
        observation_preview.add_argument("--receipt-file")
        observation_preview.add_argument("--sec-observation", required=True)
        observation_preview.add_argument("--sec-evidence", required=True)
        observation_preview.add_argument("--sec-impact")
        observation_preview.add_argument("--sec-handling")
        observation_preview.add_argument("--sec-followup")
        observation_preview.add_argument("--kind-hint", choices=("decision",))
        observation_preview.add_argument("--source-ref", action="append", default=[])
        observation_preview.add_argument("--tag", action="append", default=[])
        observation_preview.add_argument("--search-term", action="append", default=[])
        observation_preview.add_argument("--json", action="store_true")
    observation_read = observation_sub.add_parser("read")
    observation_read.add_argument("--id", required=True)
    observation_read.add_argument("--section", action="append", default=[])
    observation_read.add_argument("--max-bytes", type=int)
    observation_read.add_argument("--json", action="store_true")
    observation_search_parser = observation_sub.add_parser("search")
    observation_search_parser.add_argument("--query", default="")
    observation_search_parser.add_argument("--include-history", action="store_true")
    observation_search_parser.add_argument("--limit", type=int, default=8)
    observation_search_parser.add_argument("--json", action="store_true")
    annotate = observation_sub.add_parser("annotate")
    annotate.add_argument("--id", required=True)
    annotate.add_argument("--title")
    annotate.add_argument("--summary")
    annotate.add_argument("--tag", action="append")
    annotate.add_argument("--search-term", action="append")
    annotate.add_argument("--source-ref", action="append")
    annotate.add_argument("--related", action="append")
    annotate.add_argument("--clear", action="append", default=[])
    annotate.add_argument("--receipt-file")
    annotate.add_argument("--json", action="store_true")
    reverify = observation_sub.add_parser("reverify")
    reverify.add_argument("--id", required=True)
    reverify.add_argument("--verified-at", required=True)
    reverify.add_argument("--evidence-ref", required=True)
    reverify.add_argument("--receipt-file")
    reverify.add_argument("--json", action="store_true")
    invalidate = observation_sub.add_parser("invalidate")
    invalidate.add_argument("--id", required=True)
    invalidate.add_argument("--reason", required=True)
    invalidate.add_argument("--receipt-file")
    invalidate.add_argument("--json", action="store_true")
    supersede = observation_sub.add_parser("supersede")
    supersede.add_argument("--id", required=True)
    supersede.add_argument("--successor-result", required=True)
    supersede.add_argument("--lifecycle-input", required=True)
    supersede.add_argument("--lifecycle-attestation", required=True)
    supersede.add_argument("--receipt-file")
    supersede.add_argument("--json", action="store_true")
    observation_discard = observation_sub.add_parser("discard")
    observation_discard.add_argument("--id", required=True)
    observation_discard.add_argument("--receipt-file")
    observation_discard.add_argument("--json", action="store_true")
    archive = sub.add_parser("archive")
    archive_sub = archive.add_subparsers(dest="archive_command", required=True)
    archive_preview = archive_sub.add_parser(
        "preview",
        help="Build and freeze an immutable archive approval preview without recording it.",
    )
    archive_preview.add_argument("--title", required=True)
    archive_preview.add_argument("--summary", required=True)
    archive_preview.add_argument("--filename")
    archive_preview.add_argument("--captured-from", choices=("conversation", "workspace", "manual", "import"), required=True)
    archive_preview.add_argument("--attestation")
    archive_preview.add_argument("--attest-source-adopted", action="store_true")
    archive_preview.add_argument("--attest-immutable-original", action="store_true")
    archive_preview.add_argument("--receipt-file")
    archive_preview.add_argument("--content", required=True, help="Literal content or @file; @- reads stdin.")
    archive_preview.add_argument("--source-ref", action="append", required=True)
    archive_preview.add_argument("--tag", action="append", default=[])
    archive_preview.add_argument("--search-term", action="append", default=[])
    archive_preview.add_argument("--json", action="store_true")
    archive_read_parser = archive_sub.add_parser("read")
    archive_read_parser.add_argument("--id", required=True)
    archive_read_parser.add_argument("--max-bytes", type=int)
    archive_read_parser.add_argument("--json", action="store_true")
    archive_search_parser = archive_sub.add_parser("search")
    archive_search_parser.add_argument("--query", default="")
    archive_search_parser.add_argument("--limit", type=int, default=8)
    archive_search_parser.add_argument("--json", action="store_true")
    archive_discard = archive_sub.add_parser("discard")
    archive_discard.add_argument("--id", required=True)
    archive_discard.add_argument("--receipt-file")
    archive_discard.add_argument("--json", action="store_true")
    refresh = sub.add_parser("refresh")
    refresh.add_argument("--fix", choices=("index",))
    refresh.add_argument("--check", action="store_true", help="CI mode: exit non-zero when derived indexes drift from the artifacts or integrity issues exist.")
    refresh.add_argument("--json", action="store_true")
    return parser


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "schema":
        return schema_result()
    if args.command == "capabilities":
        return capabilities_result()
    repo = _vault_root(getattr(args, "vault", None))
    if args.command == "settings":
        settings = project_settings(repo)
        return {**settings, "project": str(settings["project"]), "vault": str(repo),
                "builtin_features": list(BUILTIN_AREAS), "configured": settings["config"] is not None}
    if args.command == "doctor":
        return doctor_repository(repo)
    if args.command == "init":
        return bootstrap_repository(repo, host=args.host)
    if args.command == "bootstrap":
        return bootstrap_repository(
            repo,
            _load_descriptor_argument(args.descriptor, allow_stdin=True),
            _load_text_argument(args.index_seed),
            host=args.host,
        )
    if args.command == "draft":
        candidate = _load_json_argument(args.candidate, allow_stdin=True)
        if candidate.get("requested_kind") != args.kind:
            raise ContextError("candidate_invalid", "draft kind differs from embedded candidate")
        return draft_owner_result(
            candidate,
            _load_json_argument(args.attestation),
        )
    if args.command == "lifecycle" and args.lifecycle_command == "prepare":
        value = prepare_lifecycle_input(repo, args.transition, args.predecessor, _load_json_argument(args.successor_result, allow_stdin=True))
        return {"input": value, "input_digest": canonical_digest(value), "applied": False}
    if args.command == "area" and args.area_command == "register":
        return build_area_register_bundle(repo, _load_descriptor_argument(args.descriptor, allow_stdin=True), _load_text_argument(args.index_seed))
    if args.command == "transaction" and args.transaction_command == "preview":
        owner_result = _load_json_argument(args.owner_result, allow_stdin=True)
        validation = _load_json_argument(args.owner_validation) if args.owner_validation else None
        priors = [_load_json_argument(value) for value in args.prior_bundle]
        return finalize_owner_result(repo, owner_result, validation, priors)
    if args.command == "transaction" and args.transaction_command == "apply":
        authorization = {"approval_source": args.approval_source, "policy_decision": args.policy_decision, "policy_reason": args.policy_reason}
        if args.receipt_file:
            return apply_receipt(repo, args.receipt_file, args.approved_digest, **authorization)
        return apply_bundle(repo, _load_json_argument(args.plan_bundle), args.approved_digest, **authorization)
    if args.command == "candidate" and args.candidate_command == "route":
        return route_candidates(
            _load_json_argument(args.batch, allow_stdin=True),
            _load_json_argument(args.capabilities),
            _load_json_argument(args.claim_results),
            repo=repo,
        )
    if args.command == "policy" and args.policy_command == "preview":
        return build_policy_bundle(repo, args.target)
    if args.command == "recall":
        facets = []
        for value in args.facet:
            if "=" not in value:
                raise ContextError("usage_invalid", "facet must be KEY=VALUE")
            facets.append(tuple(value.split("=", 1)))
        return recall_repository(
            repo,
            query=args.query,
            areas=args.area,
            include_history=args.include_history,
            include_archive=args.include_archive,
            facets=facets,
            limit=args.limit,
            pack=args.pack,
            sections=args.section,
            read_ids=args.read,
            strict_index=args.strict_index,
            max_bytes=args.max_bytes,
        )
    if args.command == "rename":
        return _maybe_freeze_bundle_receipt(
            repo,
            build_rename_bundle(repo, args.id, args.filename),
            args.receipt_file,
        )
    if args.command == "discard":
        return _maybe_freeze_bundle_receipt(repo, build_discard_bundle(repo, args.id), args.receipt_file)
    if args.command == "snapshot":
        if args.snapshot_command == "save":
            owner_inputs: dict[str, Any] = {
                "current_context": _load_body_argument(args.sec_context),
                "open_items": _body_to_items(args.sec_open_items),
                "next_steps": _body_to_items(args.sec_next_steps),
            }
            for argument, field in (("sec_decided", "decided"), ("sec_refs", "refs"), ("sec_candidates", "capture_candidates")):
                value = getattr(args, argument)
                if value is not None:
                    owner_inputs[field] = _body_to_items(value)
            if args.anchor:
                owner_inputs["anchors"] = args.anchor
            candidate = direct_candidate(
                "snapshot",
                title=args.title,
                summary=args.summary,
                captured_from=args.captured_from,
                owner_inputs=owner_inputs,
                source_refs=args.source_ref,
                tags=args.tag,
                search_terms=args.search_term,
            )
            attestation, flag_based = _capture_attestation(args, candidate, "snapshot")
            saved = build_snapshot_save_bundle(repo, candidate, attestation, filename=args.filename)
            if args.approved:
                return apply_approved_bundle(repo, saved, args.receipt_file)
            return _maybe_freeze_bundle_receipt(repo, saved, args.receipt_file, use_default=flag_based)
        if args.snapshot_command == "update":
            sections = _section_arguments(args, (
                ("sec_context", "Current context"), ("sec_open_items", "Open items"), ("sec_next_steps", "Next steps"),
                ("sec_decided", "Decided"), ("sec_refs", "References"), ("sec_candidates", "Capture candidates"),
            ))
            updated = build_snapshot_update_bundle(
                repo,
                args.id,
                merge=args.merge,
                sections=sections,
                title=args.title,
                summary=args.summary,
                tags=args.tag,
                search_terms=args.search_term,
                source_refs=args.source_ref,
                anchors=args.anchor,
                clear=args.clear,
            )
            if args.approved:
                return apply_approved_bundle(repo, updated, args.receipt_file)
            return _maybe_freeze_bundle_receipt(repo, updated, args.receipt_file)
        if args.snapshot_command == "list":
            return snapshot_list(repo, args.limit)
        if args.snapshot_command == "search":
            return snapshot_search(repo, args.query, args.limit)
        if args.snapshot_command == "load":
            return snapshot_load(repo, args.id, args.section, args.max_bytes)
        if args.snapshot_command == "discard":
            return _maybe_freeze_bundle_receipt(
                repo,
                build_snapshot_discard_bundle(repo, args.id),
                args.receipt_file,
            )
    if args.command == "observation":
        if args.observation_command in {"preview", "capture"}:
            owner_inputs = {
                "observation": _load_body_argument(args.sec_observation),
                "evidence": _body_to_items(args.sec_evidence),
            }
            if args.sec_impact is not None:
                owner_inputs["impact"] = _load_body_argument(args.sec_impact)
            if args.sec_handling is not None:
                owner_inputs["current_handling"] = _load_body_argument(args.sec_handling)
            if args.sec_followup is not None:
                owner_inputs["followup_conditions"] = _body_to_items(args.sec_followup)
            candidate = direct_candidate(
                "observation",
                title=args.title,
                summary=args.summary,
                captured_from=args.captured_from,
                owner_inputs=owner_inputs,
                source_refs=args.source_ref,
                tags=args.tag,
                search_terms=args.search_term,
                kind_hint=args.kind_hint,
            )
            attestation, flag_based = _capture_attestation(args, candidate, "observation")
            return _maybe_freeze_bundle_receipt(
                repo,
                build_observation_capture_bundle(repo, candidate, attestation, filename=args.filename),
                args.receipt_file,
                use_default=flag_based,
            )
        if args.observation_command == "read":
            return observation_read(repo, args.id, args.section, args.max_bytes)
        if args.observation_command == "search":
            return observation_search(repo, args.query, include_history=args.include_history, limit=args.limit)
        if args.observation_command == "annotate":
            return _maybe_freeze_bundle_receipt(
                repo,
                build_observation_annotate_bundle(
                    repo,
                    args.id,
                    title=args.title,
                    summary=args.summary,
                    tags=args.tag,
                    search_terms=args.search_term,
                    source_refs=args.source_ref,
                    related=args.related,
                    clear=args.clear,
                ),
                args.receipt_file,
            )
        if args.observation_command == "reverify":
            return _maybe_freeze_bundle_receipt(
                repo,
                build_observation_reverify_bundle(repo, args.id, args.verified_at, args.evidence_ref),
                args.receipt_file,
            )
        if args.observation_command == "invalidate":
            return _maybe_freeze_bundle_receipt(
                repo,
                build_observation_invalidate_bundle(repo, args.id, args.reason),
                args.receipt_file,
            )
        if args.observation_command == "supersede":
            return _maybe_freeze_bundle_receipt(
                repo,
                build_observation_supersede_bundle(
                    repo,
                    args.id,
                    _load_json_argument(args.successor_result, allow_stdin=True),
                    _load_json_argument(args.lifecycle_input),
                    _load_json_argument(args.lifecycle_attestation),
                ),
                args.receipt_file,
            )
        if args.observation_command == "discard":
            return _maybe_freeze_bundle_receipt(
                repo,
                build_observation_discard_bundle(repo, args.id),
                args.receipt_file,
            )
    if args.command == "archive":
        if args.archive_command == "preview":
            candidate = direct_candidate(
                "archive",
                title=args.title,
                summary=args.summary,
                captured_from=args.captured_from,
                owner_inputs={
                    "content": _load_body_argument(args.content, MAX_ARCHIVE_INPUT_BYTES),
                },
                source_refs=args.source_ref,
                tags=args.tag,
                search_terms=args.search_term,
            )
            attestation, flag_based = _capture_attestation(args, candidate, "archive")
            return _maybe_freeze_bundle_receipt(
                repo,
                build_archive_capture_bundle(repo, candidate, attestation, filename=args.filename),
                args.receipt_file,
                use_default=flag_based,
            )
        if args.archive_command == "read":
            return archive_read(repo, args.id, max_bytes=args.max_bytes)
        if args.archive_command == "search":
            return archive_search(repo, args.query, args.limit)
        if args.archive_command == "discard":
            return _maybe_freeze_bundle_receipt(
                repo,
                build_archive_discard_bundle(repo, args.id),
                args.receipt_file,
            )
    if args.command == "refresh":
        if args.fix:
            return repair_derived_indexes(repo)
        result = refresh_repository(repo)
        if getattr(args, "check", False):
            drift = [
                warning for warning in result["warnings"]
                if str(warning.get("code", "")).startswith("index_") or warning.get("code") in {"root_index_drift", "area_index_mismatch"}
            ]
            if result["issues"]:
                raise ContextError("integrity_issues", "context integrity issues are present", {"issues": result["issues"], "drift": drift}, EXIT_INTEGRITY)
            if drift:
                raise ContextError("projection_drift", "derived indexes differ from the artifacts; run `refresh --fix index` and commit the result", {"drift": drift}, EXIT_INTEGRITY)
            return {**result, "check": "ok"}
        return result
    raise ContextError("usage_invalid", "unsupported command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        with _config_call("project_environment", args.project):
            result = _dispatch(args)
        envelope = {"ok": True, "result": result}
        if result.get("state") == "awaiting_approval" and result.get("applied") is False:
            envelope.update({"applied": False, "state": "awaiting_approval"})
        if getattr(args, "json", False):
            print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result.get("state") == "awaiting_approval" and result.get("applied") is False:
                print("Not recorded yet — approval and transaction apply are required.")
        return 0
    except ContextError as error:
        print(json.dumps(error.envelope(), ensure_ascii=False, separators=(",", ":")), file=sys.stdout)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
