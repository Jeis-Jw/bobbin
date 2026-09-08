"""Project-local Bobbin configuration; physical writes are delegated to core."""
from __future__ import annotations

import hashlib
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

SCHEMA = "bobbin-project/v1"
FEATURES = ("decision", "assumption", "term", "intent", "document")
BUILTINS = ("snapshot", "observation", "archive")
MODES = ("explicit", "auto", "adaptive")
CONFIG_PATH = ".bobbin/config.json"


class ConfigError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def project_root(vault=None, project=None):
    selected = project or os.environ.get("BOBBIN_PROJECT_ROOT")
    if selected:
        try:
            root = Path(selected).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ConfigError("project_invalid", "Project directory does not exist.") from error
        if not root.is_dir():
            raise ConfigError("project_invalid", "Project must be a directory.")
        return root
    cwd = Path.cwd().resolve()
    candidate = cwd
    for root in (cwd, *cwd.parents):
        if (root / ".bobbin").exists() or (root / ".bobbin").is_symlink():
            return root
        if (root / ".git").exists():
            candidate = root
            break
    # A configured calling project owns its policy even when its vault is
    # elsewhere. Only unconfigured legacy callers fall back to a named vault.
    if vault is not None and not cwd.is_relative_to(Path(vault).resolve()):
        return Path(vault).resolve()
    return candidate


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("config_invalid", f"Duplicate configuration key: {key}")
        result[key] = value
    return result


def project_environment(project=None):
    """Pin the calling project across a CLI and its embedded/subprocess writers."""
    root = project_root(project=project)

    @contextlib.contextmanager
    def pinned():
        previous = os.environ.get("BOBBIN_PROJECT_ROOT")
        os.environ["BOBBIN_PROJECT_ROOT"] = str(root)
        try:
            yield root
        finally:
            if previous is None:
                os.environ.pop("BOBBIN_PROJECT_ROOT", None)
            else:
                os.environ["BOBBIN_PROJECT_ROOT"] = previous

    return pinned()


def validate(config):
    if not isinstance(config, dict) or set(config) != {"schema", "features", "approval", "vault"}:
        raise ConfigError("config_invalid", "Expected schema, features, approval and vault fields.")
    if config["schema"] != SCHEMA:
        raise ConfigError("config_invalid", "Unsupported Bobbin project schema.")
    features = config["features"]
    if (not isinstance(features, list) or any(not isinstance(x, str) or x not in FEATURES for x in features)
            or len(features) != len(set(features))):
        raise ConfigError("config_invalid", "Features must be a unique list of supported semantic owners.")
    approval = config["approval"]
    if not isinstance(approval, dict) or set(approval) != {"mode"} or approval["mode"] not in MODES:
        raise ConfigError("config_invalid", "Approval mode must be explicit, auto or adaptive.")
    if not isinstance(config["vault"], str) or not config["vault"] or "\x00" in config["vault"]:
        raise ConfigError("config_invalid", "Vault must be a directory path relative to the project or absolute.")
    return config


def load(vault=None, project=None):
    root = project_root(vault, project)
    directory = root / ".bobbin"
    path = root / CONFIG_PATH
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ConfigError("config_unsafe", ".bobbin must be a real directory.")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return {"project": root, "config": None, "digest": None, "mode": "explicit", "enabled": None}
    except OSError as error:
        raise ConfigError("config_unsafe", "Cannot safely read Bobbin configuration.") from error
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > 8192:
            raise ConfigError("config_unsafe", "Configuration must be a regular file of at most 8 KiB.")
        content = os.read(fd, 8193)
        if len(content) > 8192:
            raise ConfigError("config_invalid", "Configuration is too large.")
        config = validate(json.loads(content.decode("utf-8"), object_pairs_hook=_object))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ConfigError("config_invalid", "Configuration must be valid UTF-8 JSON.") from error
    finally:
        os.close(fd)
    return {"project": root, "config": config, "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            "mode": config["approval"]["mode"], "enabled": (*BUILTINS, *config["features"])}


def configured_vault():
    settings = load()
    if settings["config"] is None:
        return None
    try:
        vault = (settings["project"] / settings["config"]["vault"]).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ConfigError("vault_not_found", "Configured vault directory does not exist.") from error
    if not vault.is_dir():
        raise ConfigError("vault_not_found", "Configured vault is not a directory.")
    return vault


def binding(core, vault):
    settings = load(vault)
    # Bind even the absence of config, so enabling/reconfiguring cannot authorize
    # a pending proposal prepared under another project or policy.
    return {"project_identity": core.vault_identity(settings["project"]), "config_digest": settings["digest"],
            "runtime_digest": core.sha256_bytes(Path(__file__).read_bytes())}


def require_enabled(vault, areas):
    settings = load(vault)
    if settings["enabled"] is not None:
        disabled = set(areas) - set(settings["enabled"])
        if disabled:
            raise ConfigError("feature_disabled", "Enable these Bobbin features before recording: " + ", ".join(sorted(disabled)))


def authorize(core, vault, bundle, source, decision=None, reason=None):
    settings = load(vault)
    plan = bundle["approval_material"]["plan"]
    content_write = plan.get("source_type") == "owner_result"
    if content_write:
        require_enabled(vault, [effect["area"] for effect in bundle["approval_material"]["preview"]["effects"] if effect.get("area")])
    if source in {"user", "explicit_init"}:
        return {"source": source, "mode": settings["mode"]}
    if source != "policy" or not content_write or settings["config"] is None or settings["mode"] == "explicit":
        raise ConfigError("approval_required", "This operation requires explicit user authorization.")
    configured = (settings["project"] / settings["config"]["vault"]).resolve()
    if configured != Path(vault).resolve():
        raise ConfigError("vault_policy_mismatch", "Automatic recording is limited to this project's configured vault.")
    if settings["mode"] == "adaptive":
        if decision not in {"record", "ask"} or not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ConfigError("policy_assessment_required", "Adaptive recording requires a record/ask assessment and a concise reason.")
        if decision == "ask":
            raise ConfigError("approval_required", reason)
    return {"source": "policy", "mode": settings["mode"], "decision": "record",
            "reason": reason or "Project is configured for automatic recording.", "config_digest": settings["digest"]}


def _owner_plan(kind):
    path = Path(__file__).resolve().parents[2] / kind / "scripts" / f"{kind}_cli.py"
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"bobbin_init_{kind}", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.build_init_plan()
    finally:
        sys.path.pop(0)


def initialize(core, *, project=None, vault=None, features=None, approval_mode=None, host):
    root = project_root(project=project)
    old = load(project=root)
    if features is None:
        selected = list(old["config"]["features"]) if old["config"] else None
    else:
        selected = list(features)
    mode = approval_mode or old["mode"]
    previous_vault = next((p for p in (root, *root.parents) if (p / "context").exists()), root)
    vault_path = Path(vault).expanduser() if vault else (root / old["config"]["vault"] if old["config"] else previous_vault)
    try:
        vault_path = vault_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ConfigError("vault_not_found", "Create or select an existing vault directory.") from error
    if not vault_path.is_dir():
        raise ConfigError("vault_not_found", "Vault must be a directory.")
    import_features = selected is None
    if import_features:
        index = vault_path / core.ROOT_INDEX
        if index.exists():
            _, areas = core.parse_root_index(index.read_text(encoding="utf-8"))
            selected = [kind for kind in FEATURES if any(row["area"] == kind for row in areas)]
        else:
            selected = ["decision"]
    config = validate({"schema": SCHEMA, "features": selected, "approval": {"mode": mode},
                       "vault": os.path.relpath(vault_path, root)})
    plans = [_owner_plan(kind) for kind in selected]
    # Preflight the project guidance before any vault mutation.
    target = core.POLICY_HOST_TARGETS[host]
    core.build_policy_bundle(root, target)
    merge_attributes = core._git_metadata_root(vault_path) is not None
    if merge_attributes:
        core.build_policy_bundle(vault_path, core.MERGE_ATTRIBUTES_TARGET)
    changed = []
    phases = []

    def apply_seed(name, destination, result):
        if result.get("noop"):
            phases.append({"phase": name, "status": "noop"})
            return
        applied = core.apply_bundle(destination, result["bundle"], result["approval_digest"], approval_source="explicit_init")
        changed.extend(str(destination / path) for path in applied["changed_paths"])
        phases.append({"phase": name, "status": "applied"})

    with core._root_lock(root / ".bobbin"):
        if load(project=root)["digest"] != old["digest"]:
            raise ConfigError("config_changed", "Project settings changed during initialization; retry.")
        context_root = vault_path / "context"
        if context_root.is_dir() and not (vault_path / core.ROOT_INDEX).is_file() and any(context_root.glob("*/*.index.md")):
            repaired = core.repair_derived_indexes(vault_path)
            changed.extend(str(vault_path / path) for path in repaired["changed_paths"])
            if import_features:
                _, areas = core.parse_root_index((vault_path / core.ROOT_INDEX).read_text(encoding="utf-8"))
                selected = [kind for kind in FEATURES if any(row["area"] == kind for row in areas)]
                config["features"] = selected
                plans = [_owner_plan(kind) for kind in selected]
        pending_area = any(core._pending_area_resume_bundle(vault_path, plan["owner_descriptor"], plan["index_seed"]) is not None for plan in plans)
        if pending_area:
            phases.append({"phase": "core_init", "status": "noop"})
        else:
            apply_seed("core_init", vault_path, core.build_init_bundle(vault_path))
        for plan in plans:
            apply_seed("area_register:" + plan["owner_descriptor"]["kind"], vault_path,
                       core.build_area_register_bundle(vault_path, plan["owner_descriptor"], plan["index_seed"]))
        if merge_attributes:
            apply_seed("merge_attributes_install", vault_path, core.build_policy_bundle(vault_path, core.MERGE_ATTRIBUTES_TARGET))
        apply_seed("policy_install", root, core.build_policy_bundle(root, target))
        if old["config"] != config:
            # Recheck path safety immediately before the sole-writer operation.
            load(project=root)
            core._atomic_write(root / CONFIG_PATH, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            changed.append(str(root / CONFIG_PATH))
    return {"schema": "bobbin-init-result/v1", "version": "1.0.0", "project": str(root), "vault": str(vault_path),
            "config": config, "enabled": [*BUILTINS, *selected], "phases": phases,
            "changed_paths": sorted(set(changed)), "host_configuration_changed": False,
            "records_migrated": False, "applied": bool(changed)}
