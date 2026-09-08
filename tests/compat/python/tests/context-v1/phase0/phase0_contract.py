"""Host-independent Phase 0 probes and filesystem mechanism harnesses.

This module intentionally lives under tests. It freezes probe/renderer and
mechanism contracts without becoming a second production implementation.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path


class InventoryContractError(ValueError):
    pass


class MechanismContractError(RuntimeError):
    pass


REQUIRED_PLUGIN_FIELDS = (
    "marketplace",
    "plugin",
    "selector",
    "source",
    "provider",
    "required_protocol",
    "entrypoint",
    "compatible_major",
)
OBSERVED_FIELDS = (
    "marketplace",
    "plugin",
    "source",
    "enabled",
    "protocol",
    "repository_state",
)
ENTRY_KEY_ORDER = (
    "id",
    "path",
    "title",
    "summary",
    "state",
    "created_at",
    "updated_at",
    "terms",
    "retired_at",
    "retired_reason",
    "superseded_by",
    "scope",
    "decision_key",
    "revisit_on",
)
OWNER_DESCRIPTOR_FIELDS = (
    "schema",
    "owner",
    "kind",
    "artifact_schema",
    "authority",
    "claim_surface",
)


def _required_plugin(value):
    if not isinstance(value, dict) or tuple(value) != REQUIRED_PLUGIN_FIELDS:
        raise InventoryContractError("required_plugin_invalid")
    string_fields = tuple(field for field in REQUIRED_PLUGIN_FIELDS if field != "compatible_major")
    if any(not isinstance(value[field], str) or not value[field] for field in string_fields):
        raise InventoryContractError("required_plugin_invalid")
    if not isinstance(value["compatible_major"], int) or isinstance(value["compatible_major"], bool) or value["compatible_major"] < 0:
        raise InventoryContractError("required_plugin_invalid")
    return value


def _owner_descriptor(value):
    if not isinstance(value, dict):
        raise InventoryContractError("owner_descriptor_invalid")
    if any(field not in value for field in OWNER_DESCRIPTOR_FIELDS):
        raise InventoryContractError("owner_descriptor_invalid")
    if value.get("schema") != "context-owner-capability/v1":
        raise InventoryContractError("owner_descriptor_invalid")
    return copy.deepcopy(value)


def discover_owner(probe):
    """Resolve only host inventory or an explicit caller descriptor.

    Process discovery, plugin cache probing, and alternate runtimes are not
    accepted discovery surfaces.
    """

    if not isinstance(probe, dict):
        raise InventoryContractError("owner_discovery_invalid")
    source = probe.get("source")
    if source == "caller_descriptor":
        return _owner_descriptor(probe.get("descriptor"))
    if source == "installed_skill_inventory":
        kind = probe.get("kind")
        inventory = probe.get("inventory")
        if not isinstance(kind, str) or not isinstance(inventory, list):
            raise InventoryContractError("owner_inventory_invalid")
        matches = [item for item in inventory if isinstance(item, dict) and item.get("kind") == kind]
        if len(matches) != 1:
            raise InventoryContractError("owner_inventory_ambiguous")
        return _owner_descriptor(matches[0])
    if source in {"cache_path_probe", "runtime_process", "alternate_runtime"}:
        raise InventoryContractError("owner_discovery_surface_forbidden")
    raise InventoryContractError("owner_discovery_surface_unknown")


def _observed(plugin, doctor, required):
    protocols = plugin.get("protocols", []) if isinstance(plugin, dict) else []
    doctor_protocols = doctor.get("supported_protocols", []) if isinstance(doctor, dict) else []
    compatible = (
        required["required_protocol"] in protocols
        and required["required_protocol"] in doctor_protocols
    )
    if compatible:
        protocol = required["required_protocol"]
    elif protocols:
        protocol = protocols[0]
    elif doctor_protocols:
        protocol = doctor_protocols[0]
    else:
        protocol = None
    result = {
        "marketplace": plugin.get("marketplace") if isinstance(plugin, dict) else None,
        "plugin": plugin.get("plugin") if isinstance(plugin, dict) else None,
        "source": plugin.get("source") if isinstance(plugin, dict) else None,
        "enabled": plugin.get("enabled") if isinstance(plugin, dict) else None,
        "protocol": protocol,
        "repository_state": doctor.get("repository_state") if isinstance(doctor, dict) else None,
    }
    assert tuple(result) == OBSERVED_FIELDS
    return result


def classify_preflight(inventory, doctor, required_plugin):
    """Classify the manual dependency preflight in its canonical order."""

    required = _required_plugin(required_plugin)
    if not isinstance(inventory, dict) or not isinstance(inventory.get("plugins"), list):
        raise InventoryContractError("host_inventory_invalid")
    if not isinstance(doctor, dict):
        raise InventoryContractError("doctor_receipt_invalid")

    plugins = inventory["plugins"]
    exact_coordinate = [
        item
        for item in plugins
        if isinstance(item, dict)
        and item.get("marketplace") == required["marketplace"]
        and item.get("plugin") == required["plugin"]
    ]
    same_name = [
        item
        for item in plugins
        if isinstance(item, dict) and item.get("plugin") == required["plugin"]
    ]

    if len(exact_coordinate) > 1:
        raise InventoryContractError("exact_plugin_ambiguous")
    if not exact_coordinate:
        observed_plugin = same_name[0] if len(same_name) == 1 else None
        code = "core_source_mismatch" if observed_plugin is not None else "core_missing"
        return {"code": code, "observed": _observed(observed_plugin, doctor, required)}

    plugin = exact_coordinate[0]
    observed = _observed(plugin, doctor, required)
    if plugin.get("source") != required["source"]:
        return {"code": "core_source_mismatch", "observed": observed}
    if plugin.get("enabled") is not True:
        return {"code": "core_disabled", "observed": observed}

    plugin_protocols = plugin.get("protocols")
    doctor_protocols = doctor.get("supported_protocols")
    if (
        not isinstance(plugin_protocols, list)
        or not isinstance(doctor_protocols, list)
        or required["required_protocol"] not in plugin_protocols
        or required["required_protocol"] not in doctor_protocols
    ):
        return {"code": "core_incompatible", "observed": observed}

    repository_state = doctor.get("repository_state")
    if repository_state not in {"absent", "partial", "invalid", "ready"}:
        raise InventoryContractError("repository_state_invalid")
    if repository_state == "absent":
        return {"code": "core_uninitialized", "observed": observed}
    issues = doctor.get("issues", [])
    warnings = doctor.get("warnings", [])
    if not isinstance(issues, list) or not isinstance(warnings, list):
        raise InventoryContractError("repository_diagnostics_invalid")
    diagnostics = [
        {"repository_state": repository_state, **item}
        for item in [*issues, *warnings]
        if isinstance(item, dict)
    ]
    return {"code": "ready", "observed": observed, "warnings": diagnostics}


_MESSAGES = {
    "core_missing": "The exact context-core is absent from the current host inventory.",
    "core_source_mismatch": "A same-named core has a different marketplace or source coordinate.",
    "core_disabled": "The exact context-core is disabled in the current scope.",
    "core_incompatible": "The exact context-core failed the context-common/v2 handshake.",
    "core_uninitialized": "The exact core is ready but the repository requires bootstrap.",
    "ready": "The exact context-core is ready; repository diagnostics block only when they overlap the target.",
}


def _manual_actions(code, required):
    selector = required["selector"]
    provider = required["marketplace"]
    source = required["source"]
    retry = "Reload the host or start a new session, then run bobbin:init again."
    actions = {
        "core_missing": [
            f"Install {selector} explicitly from provider marketplace {provider} (source {source}).",
            "Choose the installation scope explicitly.",
            retry,
        ],
        "core_source_mismatch": [
            f"Install the exact {selector} coordinate from source {source} explicitly.",
            "A same-named plugin from another marketplace does not satisfy the requirement.",
            retry,
        ],
        "core_disabled": [
            f"Enable the exact {selector} explicitly in the intended scope.",
            retry,
        ],
        "core_incompatible": [
            f"Update the exact {selector} explicitly to a {required['required_protocol']}-compatible version.",
            retry,
        ],
        "core_uninitialized": [
            "bobbin:init calls the installed Bobbin core public bootstrap surface.",
            "The same explicit invocation continues with decision-area registration after core init.",
        ],
        "ready": [],
    }
    return actions[code]


def render_preflight(result, host, required_plugin):
    required = _required_plugin(required_plugin)
    if host not in {"codex", "claude-code"}:
        raise InventoryContractError("host_unknown")
    if not isinstance(result, dict) or result.get("code") not in _MESSAGES:
        raise InventoryContractError("preflight_result_invalid")
    observed = result.get("observed")
    if not isinstance(observed, dict) or tuple(observed) != OBSERVED_FIELDS:
        raise InventoryContractError("preflight_result_invalid")
    rendered = {
        "code": result["code"],
        "host": host,
        "message": _MESSAGES[result["code"]],
        "required_plugin": copy.deepcopy(required),
        "observed": copy.deepcopy(observed),
        "manual_actions": _manual_actions(result["code"], required),
        "write_policy": {"repository": "none", "host_configuration": "none"},
    }
    if result.get("warnings"):
        rendered["warnings"] = copy.deepcopy(result["warnings"])
    return rendered


def _sha256(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def repository_root_wikilink(path):
    if not isinstance(path, str) or not path.startswith("context/") or not path.endswith(".md"):
        raise MechanismContractError("index_path_invalid")
    if path.startswith("/") or ".." in Path(path).parts or "\\" in path:
        raise MechanismContractError("index_path_invalid")
    return "[[" + path[:-3] + "]]"


def _markdown_inline(value):
    return re.sub(r"([\\`*_{}\[\]<>#|])", r"\\\1", value)


def index_row_from_entry(entry):
    if not isinstance(entry, dict) or any(key not in ENTRY_KEY_ORDER for key in entry):
        raise MechanismContractError("index_entry_invalid")
    required = {"id", "path", "title", "summary", "state", "created_at", "terms"}
    if not required.issubset(entry):
        raise MechanismContractError("index_entry_invalid")
    ordered = {key: entry[key] for key in ENTRY_KEY_ORDER if key in entry}
    machine = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    return (
        f"- {repository_root_wikilink(entry['path'])} — "
        f"{_markdown_inline(entry['title'])} — {_markdown_inline(entry['summary'])} "
        f"<!-- context-entry {machine} -->"
    )


_ROW_PATTERN = re.compile(
    r"^- (?P<link>\[\[context/.+\]\]) — .+ — .+ <!-- context-entry (?P<json>\{.+\}) -->$"
)


def index_row_to_entry(row):
    if not isinstance(row, str) or "\n" in row:
        raise MechanismContractError("index_row_invalid")
    match = _ROW_PATTERN.fullmatch(row)
    if match is None:
        raise MechanismContractError("index_row_invalid")
    try:
        entry = json.loads(match.group("json"))
    except json.JSONDecodeError as exc:
        raise MechanismContractError("index_row_invalid") from exc
    if repository_root_wikilink(entry.get("path")) != match.group("link"):
        raise MechanismContractError("index_link_mismatch")
    if index_row_from_entry(entry) != row:
        raise MechanismContractError("index_row_noncanonical")
    return entry


def slug_filename(title):
    if not isinstance(title, str):
        raise MechanismContractError("filename_required")
    normalized = unicodedata.normalize("NFC", title.strip())
    output = []
    in_separator = False
    for character in normalized:
        if character.isalnum() or character in "-_.":
            output.append(character)
            in_separator = False
        elif not in_separator:
            output.append("-")
            in_separator = True
    stem = "".join(output).strip("-._")
    if not stem or len(stem) > 120 or len(stem.encode("utf-8")) > 240:
        raise MechanismContractError("filename_required")
    return stem + ".md"


def collision_key(basename):
    if not isinstance(basename, str):
        raise MechanismContractError("filename_invalid")
    return unicodedata.normalize("NFKC", basename).casefold()


def _fsync_directory(path):
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _durable_temp_replace(target, after_bytes, replace_observer=None):
    descriptor, temp_name = tempfile.mkstemp(prefix=".context-v1-", dir=target.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(after_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        if replace_observer is not None:
            replace_observer(temp_name, target)
        os.replace(temp_name, target)
        _fsync_directory(target.parent)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_replace_exact(target, after_bytes, before_digest, replace_observer=None):
    target = Path(target)
    if not target.is_file() or _sha256(target.read_bytes()) != before_digest:
        raise MechanismContractError("precondition_changed")
    _durable_temp_replace(target, after_bytes, replace_observer)


def changed_move(
    source,
    destination,
    *,
    before_digest,
    after_bytes,
    crash_after_prepare=False,
):
    source = Path(source)
    destination = Path(destination)
    source_digest = _sha256(source.read_bytes()) if source.is_file() else None
    destination_digest = _sha256(destination.read_bytes()) if destination.is_file() else None
    after_digest = _sha256(after_bytes)

    if source_digest is None and destination_digest == after_digest:
        return "already_final"
    if source_digest == before_digest and destination_digest is None:
        _durable_temp_replace(destination, after_bytes)
        if crash_after_prepare:
            raise RuntimeError("forced_crash_after_prepare")
        if _sha256(source.read_bytes()) != before_digest:
            raise MechanismContractError("precondition_changed")
        os.unlink(source)
        _fsync_directory(source.parent)
        return "completed_start"
    if source_digest == before_digest and destination_digest == after_digest:
        os.unlink(source)
        _fsync_directory(source.parent)
        return "resumed_prepared"
    raise MechanismContractError("precondition_changed")


def try_fcntl_lock_worker(lock_path, hold, ready, release, queue):
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        operation = fcntl.LOCK_EX if hold else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError:
            queue.put("blocked")
            return
        if hold:
            ready.set()
            release.wait(5)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        queue.put("acquired")
    finally:
        os.close(descriptor)


def repository_lock_path(repository):
    realpath = str(Path(repository).resolve())
    key = hashlib.sha256(realpath.encode("utf-8")).hexdigest()
    parent = Path(tempfile.gettempdir()) / "context-core-locks"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    return parent / key


@contextmanager
def _repository_lock(repository):
    lock_path = repository_lock_path(repository)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        mode = os.fstat(descriptor).st_mode & 0o777
        if mode & 0o022:
            raise MechanismContractError("lock_mode_unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def parallel_index_mutation_worker(repository, index_path, row, start):
    start.wait(5)
    index_path = Path(index_path)
    with _repository_lock(repository):
        before = index_path.read_bytes()
        rows = json.loads(before.decode("utf-8"))
        if any(item["id"] == row["id"] for item in rows):
            raise MechanismContractError("duplicate_id")
        rows.append(row)
        rows.sort(key=lambda item: item["id"])
        after = (json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        atomic_replace_exact(index_path, after, _sha256(before))
