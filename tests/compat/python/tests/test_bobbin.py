from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/bobbin"
CORE = PLUGIN / "skills/context/scripts/context_cli.py"
INIT = PLUGIN / "skills/init/scripts/bobbin_init.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = load("bobbin_test_core", CORE)
config = load("bobbin_test_config", CORE.with_name("bobbin_config.py"))
support = load("bobbin_test_observation", ROOT / "tests/owners/core/test_transaction_coordinator.py")


def initialize(project, *, mode="explicit", features=None, vault=None, host="codex"):
    return config.initialize(core, project=project, vault=vault, approval_mode=mode, features=features, host=host)


def proposal(vault):
    return core.finalize_owner_result(vault, support.observation_owner_result())


def apply(vault, prepared, **kwargs):
    return core.apply_bundle(vault, prepared["bundle"], prepared["approval_digest"], **kwargs)


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def cli(path, *args, cwd, expected=0):
    completed = subprocess.run([sys.executable, str(path), *args, "--json"], cwd=cwd, capture_output=True, text=True)
    assert completed.returncode == expected, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize("host,target", [("codex", "AGENTS.md"), ("claude-code", "CLAUDE.md")])
def test_fresh_init_and_idempotence(tmp_path, host, target):
    result = initialize(tmp_path, host=host)
    assert result["config"]["features"] == ["decision"]
    assert result["config"]["approval"]["mode"] == "explicit"
    assert result["host_configuration_changed"] is False
    before = contents(tmp_path)
    again = config.initialize(core, project=tmp_path, host=host)
    assert again["changed_paths"] == []
    assert contents(tmp_path) == before
    assert ".bobbin/config.json" in (tmp_path / target).read_text()


def test_first_init_imports_existing_areas_without_rewriting_records(tmp_path):
    core.bootstrap_repository(tmp_path, host="codex")
    owner = config._owner_plan("term")
    core.bootstrap_repository(tmp_path, owner["owner_descriptor"], owner["index_seed"], host="codex")
    apply(tmp_path, proposal(tmp_path))
    before = contents(tmp_path / "context")
    result = config.initialize(core, project=tmp_path, host="codex")
    assert result["config"]["features"] == ["term"]
    assert result["config"]["approval"]["mode"] == "explicit"
    assert contents(tmp_path / "context") == before


@pytest.mark.parametrize("mode,source,decision,reason,allowed", [
    ("explicit", "user", None, None, True),
    ("explicit", "policy", None, None, False),
    ("auto", "policy", None, None, True),
    ("auto", "user", None, None, True),
    ("adaptive", "policy", "record", "Reproduced evidence; no decision changes.", True),
    ("adaptive", "policy", "ask", "Scope is unclear.", False),
    ("adaptive", "policy", None, None, False),
    ("adaptive", "policy", "record", "", False),
    ("adaptive", "user", None, None, True),
])
def test_approval_modes_keep_integrity(tmp_path, mode, source, decision, reason, allowed):
    initialize(tmp_path, mode=mode)
    prepared = proposal(tmp_path)
    before = contents(tmp_path)
    if allowed:
        result = apply(tmp_path, prepared, approval_source=source, policy_decision=decision, policy_reason=reason)
        assert result["authorization"]["source"] == source
        assert result["authorization"]["mode"] == mode
        assert core.refresh_repository(tmp_path)["ok"]
    else:
        with pytest.raises(core.ContextError):
            apply(tmp_path, prepared, approval_source=source, policy_decision=decision, policy_reason=reason)
        assert contents(tmp_path) == before


def test_auto_cannot_authorize_missing_configuration_or_settings_mutation(tmp_path):
    core.bootstrap_repository(tmp_path, host="codex")
    with pytest.raises(core.ContextError, match="explicit user authorization"):
        apply(tmp_path, proposal(tmp_path), approval_source="policy")
    initialize(tmp_path, mode="auto")
    policy = core.build_policy_bundle(tmp_path, "CLAUDE.md")
    with pytest.raises(core.ContextError):
        apply(tmp_path, policy, approval_source="policy")
    assert not (tmp_path / "CLAUDE.md").exists()


def test_pending_record_invalidated_by_configuration_change(tmp_path):
    initialize(tmp_path, mode="auto")
    prepared = proposal(tmp_path)
    initialize(tmp_path, mode="explicit")
    before = contents(tmp_path)
    with pytest.raises(core.ContextError) as caught:
        apply(tmp_path, prepared, approval_source="policy")
    assert caught.value.code == "project_policy_changed"
    assert contents(tmp_path) == before


def test_auto_still_rejects_payload_tampering(tmp_path):
    initialize(tmp_path, mode="auto")
    prepared = proposal(tmp_path)
    prepared["bundle"]["approval_material"]["preview"]["effects"][0]["id"] = "ctx_00000000000040008000000000000001"
    before = contents(tmp_path)
    with pytest.raises(core.ContextError) as caught:
        apply(tmp_path, prepared, approval_source="policy")
    assert caught.value.code == "approval_digest_mismatch"
    assert contents(tmp_path) == before


def test_shared_vault_settings_and_pending_receipts_are_project_bound(tmp_path, monkeypatch):
    vault, a, b = [tmp_path / x for x in ("vault", "a", "b")]
    for p in (vault, a, b):
        p.mkdir()
    initialize(a, vault=vault, mode="auto", features=["decision"])
    initialize(b, vault=vault, mode="explicit", features=["term"])
    assert not (vault / ".bobbin").exists()
    assert not (vault / "AGENTS.md").exists()
    monkeypatch.setenv("BOBBIN_PROJECT_ROOT", str(a))
    prepared = proposal(vault)
    monkeypatch.setenv("BOBBIN_PROJECT_ROOT", str(b))
    with pytest.raises(core.ContextError) as caught:
        apply(vault, prepared, approval_source="policy")
    assert caught.value.code == "project_policy_changed"
    assert core.project_settings(vault)["mode"] == "explicit"
    monkeypatch.setenv("BOBBIN_PROJECT_ROOT", str(a))
    assert apply(vault, prepared, approval_source="policy")["applied"]


@pytest.mark.parametrize("bad", [{"features": ["missing"]}, {"approval": {"mode": "anything"}}, {"features": ["decision", "decision"]}])
def test_invalid_config_does_not_fall_back_to_permissive_mode(tmp_path, bad):
    initialize(tmp_path, mode="auto")
    path = tmp_path / ".bobbin/config.json"
    value = json.loads(path.read_text())
    path.write_text(json.dumps({**value, **bad}))
    before = contents(tmp_path)
    with pytest.raises(core.ContextError):
        proposal(tmp_path)
    assert contents(tmp_path) == before


def test_symlink_config_directory_is_rejected(tmp_path):
    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    (project / ".bobbin").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(config.ConfigError):
        initialize(project, mode="auto")
    assert list(elsewhere.iterdir()) == []


def decision_record(project, *, source="policy", expected=0):
    return cli(PLUGIN / "skills/decision/scripts/decision_workflow.py", "record", "--host", "codex", "--inline",
        "--approval-source", source, *(["--approved"] if source == "user" else []),
        "--title", "Cache policy", "--summary", "Use local caching", "--scope", "example", "--decision-key", "cache",
        "--commitment-evidence", "User explicitly chose local caching.", "--sec-decision", "Use local caching.",
        "--sec-rationale", "Work offline.", "--sec-alternatives", "Remote-only cache was rejected.",
        "--attest-explicit-choice", "--attest-scope-identified", "--attest-commitment-present", cwd=project, expected=expected)


def test_disabled_owner_cannot_write_but_existing_records_remain_readable(tmp_path):
    initialize(tmp_path, mode="auto")
    result = decision_record(tmp_path)
    assert result["result"]["authorization"]["source"] == "policy"
    before = contents(tmp_path / "context/decision")
    initialize(tmp_path, mode="auto", features=[])
    failure = decision_record(tmp_path, source="user", expected=5)
    assert failure["error"]["code"] == "feature_disabled"
    assert contents(tmp_path / "context/decision") == before
    implicit = core.recall_repository(tmp_path)
    assert "context/decision/" not in json.dumps(implicit)
    explicit_recall = core.recall_repository(tmp_path, areas=["decision"])
    assert "context/decision/" in json.dumps(explicit_recall)
    blocked = cli(PLUGIN / "skills/decision/scripts/decision_cli.py", "check", "--statement", "Reconsider cache", cwd=tmp_path, expected=5)
    assert blocked["error"]["code"] == "feature_disabled"
    explicit = cli(PLUGIN / "skills/decision/scripts/decision_cli.py", "search", "--scope", "example", cwd=tmp_path)
    assert "Cache policy" in json.dumps(explicit)
    initialize(tmp_path, mode="auto", features=["decision", "intent"])
    assert contents(tmp_path / "context/decision") == before


def test_package_is_self_contained_and_has_one_public_version(tmp_path):
    copied = tmp_path / "installed/bobbin"
    shutil.copytree(PLUGIN, copied, ignore=shutil.ignore_patterns("__pycache__"))
    project = tmp_path / "project"
    project.mkdir()
    cli(copied / "skills/init/scripts/bobbin_init.py", "--host", "claude-code", "--features", "decision,term", cwd=project)
    doctor = cli(copied / "skills/context/scripts/context_cli.py", "doctor", cwd=project)
    assert doctor["result"]["plugin_version"] == "1.0.0"
    manifests = list(PLUGIN.rglob("plugin.json"))
    assert len(manifests) == 2
    for path in manifests:
        assert json.loads(path.read_text())["name"] == "bobbin"
        assert json.loads(path.read_text())["version"] == "1.0.0"
    assert len(list((PLUGIN / "skills").glob("init/SKILL.md"))) == 1
    capabilities = core.capabilities_result()["owners"]
    for kind in config.FEATURES:
        owner = load("bobbin_surface_" + kind, PLUGIN / f"skills/{kind}/scripts/{kind}_cli.py")
        capabilities.append(getattr(owner, kind + "_capability")())
    for capability in capabilities:
        assert capability["claim_surface"]["name"] == "bobbin:" + capability["kind"]
        assert (PLUGIN / "skills" / capability["kind"] / "SKILL.md").is_file()
        for lifecycle in capability.get("lifecycle_operations", {}).values():
            assert lifecycle["surface"]["name"] == "bobbin:" + capability["kind"]


def test_provider_collision_is_read_only_and_detected():
    installer = load("bobbin_test_installer", ROOT / "scripts/install_profile.py")
    profile = installer.load_profile()
    installer.validate_release_surface(profile)
    for name in ("context-core", "context-decision", "context-assumption", "context-term", "context-intent", "context-document"):
        with pytest.raises(installer.InstallProfileError):
            installer.build_install_plan(profile, "codex", "user", [], [{"name": name, "marketplaceName": "context-plugins", "enabled": True}])


def test_configured_calling_project_owns_shared_vault_policy(tmp_path, monkeypatch):
    project, vault, foreign = [tmp_path / x for x in ("project", "vault", "foreign")]
    for path in (project, vault, foreign):
        path.mkdir()
    initialize(project, vault=vault, mode="auto")
    core.bootstrap_repository(foreign, host="codex")
    monkeypatch.chdir(project)
    settings = cli(CORE, "settings", cwd=project)["result"]
    assert settings["project"] == str(project)
    assert settings["vault"] == str(vault)
    assert settings["mode"] == "auto"
    assert apply(vault, proposal(vault), approval_source="policy")["applied"]
    prepared = proposal(foreign)
    before = contents(foreign)
    with pytest.raises(core.ContextError) as caught:
        apply(foreign, prepared, approval_source="policy")
    assert caught.value.code == "vault_policy_mismatch"
    assert contents(foreign) == before
    assert config.initialize(core, project=project, host="codex")["config"]["approval"]["mode"] == "auto"


def test_unconfigured_caller_cannot_inherit_shared_vault_auto_policy(tmp_path):
    vault, project = tmp_path / "vault", tmp_path / "unconfigured"
    vault.mkdir()
    project.mkdir()
    initialize(vault, mode="auto")
    settings = cli(CORE, "--vault", str(vault), "settings", cwd=project)["result"]
    assert settings["project"] == str(project)
    assert settings["mode"] == "explicit"
    assert settings["configured"] is False
    preview = cli(CORE, "--vault", str(vault), "observation", "preview", "--title", "Scoped fact",
        "--summary", "Observed locally", "--captured-from", "workspace", "--attest-reusable-observation",
        "--attest-evidence-present", "--sec-observation", "An observed fact.", "--sec-evidence", "A test run.", cwd=project)["result"]
    before = contents(vault)
    rejected = cli(CORE, "--vault", str(vault), "transaction", "apply", "--receipt-file", preview["receipt_file"],
        "--approved-digest", preview["approval_digest"], "--approval-source", "policy", cwd=project, expected=5)
    assert rejected["error"]["code"] == "approval_required"
    assert contents(vault) == before


@pytest.mark.parametrize("kind", ["assumption", "term", "intent", "document"])
@pytest.mark.parametrize("mode", ["auto", "adaptive"])
def test_all_owner_workflows_use_policy_without_user_approval(tmp_path, kind, mode):
    cases = load("bobbin_inline_cases", ROOT / "tests/context-v1/test_owner_inline_workflows.py").CASES
    initialize(tmp_path, mode=mode, features=[kind])
    workflow = PLUGIN / f"skills/{kind}/scripts/{kind}_workflow.py"
    before = contents(tmp_path)
    preview = cli(workflow, "preview", "--host", "codex", "--inline", *cases[kind]["preview"], cwd=tmp_path)["result"]
    assert contents(tmp_path) == before
    args = ["apply", "--receipt-file", preview["receipt_file"], "--approved-digest", preview["approval_digest"], "--approval-source", "policy"]
    if mode == "adaptive":
        denied = cli(workflow, *args, "--policy-decision", "ask", "--policy-reason", "Confirm the scope.", cwd=tmp_path, expected=5)
        assert denied["error"]["code"] == "approval_required"
        assert contents(tmp_path) == before
        args += ["--policy-decision", "record", "--policy-reason", "Meaning and scope are unambiguous."]
    result = cli(workflow, *args, cwd=tmp_path)["result"]
    assert result["applied"]
    assert result["authorization"]["source"] == "policy"
    assert not Path(preview["receipt_file"]).exists()


@pytest.mark.parametrize("field,value", [("features", None), ("features", [False]), ("approval", {"mode": []}), ("vault", 42)])
def test_malformed_typed_configuration_is_rejected(tmp_path, field, value):
    initialize(tmp_path)
    path = tmp_path / ".bobbin/config.json"
    data = json.loads(path.read_text())
    path.write_text(json.dumps({**data, field: value}))
    with pytest.raises(config.ConfigError):
        config.load(project=tmp_path)


def test_duplicate_config_keys_and_missing_commitment_fail_closed(tmp_path):
    initialize(tmp_path, mode="auto")
    before = contents(tmp_path)
    incomplete = cli(PLUGIN / "skills/decision/scripts/decision_workflow.py", "record", "--host", "codex", "--inline",
        "--approval-source", "policy", "--title", "Proposal", "--summary", "Model proposal",
        "--scope", "example", "--decision-key", "cache", "--sec-decision", "Use caching.",
        "--sec-rationale", "It may help.", "--sec-alternatives", "No caching.", cwd=tmp_path, expected=2)
    assert not incomplete["ok"]
    assert contents(tmp_path) == before
    path = tmp_path / ".bobbin/config.json"
    path.write_text(path.read_text().replace('"mode": "auto"', '"mode": "auto", "mode": "explicit"'))
    with pytest.raises(config.ConfigError, match="Duplicate"):
        config.load(project=tmp_path)


@pytest.mark.parametrize("kind", ["snapshot", "observation", "archive"])
@pytest.mark.parametrize("mode", ["auto", "adaptive"])
def test_builtin_records_share_policy_and_frozen_receipts(tmp_path, kind, mode):
    initialize(tmp_path, mode=mode, features=[])
    common = ["--title", "Resume evidence", "--summary", "Preserve useful context", "--captured-from", "conversation"]
    if kind == "snapshot":
        args = ["snapshot", "save", "--attest-handoff-requested", "--attest-unfinished-context-present",
                "--sec-context", "Packaging is complete.", "--sec-open-items", "Run regression tests.", "--sec-next-steps", "Verify the copied plugin."]
    elif kind == "observation":
        args = ["observation", "preview", "--attest-reusable-observation", "--attest-evidence-present",
                "--sec-observation", "The copied package initializes.", "--sec-evidence", "Verified with a temporary project."]
    else:
        args = ["archive", "preview", "--attest-source-adopted", "--attest-immutable-original",
                "--source-ref", "test:release-evidence", "--content", "Original release evidence retained verbatim."]
    before = contents(tmp_path)
    prepared = cli(CORE, *args, *common, cwd=tmp_path)["result"]
    assert contents(tmp_path) == before
    policy = ["--policy-decision", "record", "--policy-reason", "Useful non-authoritative project context."] if mode == "adaptive" else []
    applied = cli(CORE, "transaction", "apply", "--receipt-file", prepared["receipt_file"],
                  "--approved-digest", prepared["approval_digest"], "--approval-source", "policy", *policy, cwd=tmp_path)["result"]
    assert applied["authorization"]["mode"] == mode
    assert applied["authorization"]["source"] == "policy"
    assert len([p for p in (tmp_path / "context" / kind).glob("*.md") if not p.name.endswith(".index.md")]) == 1


def test_git_merge_guidance_stays_in_vault_and_preserves_user_lines(tmp_path):
    vault, project = tmp_path / "vault", tmp_path / "project"
    vault.mkdir()
    project.mkdir()
    (vault / ".git").mkdir()
    attrs = vault / ".gitattributes"
    attrs.write_text("*.txt text\n")
    initialize(project, vault=vault)
    assert attrs.read_text().startswith("*.txt text\n")
    assert "context/**/*.index.md merge=union" in attrs.read_text()
    assert (project / "AGENTS.md").exists()
    assert not (vault / "AGENTS.md").exists()
    assert not (project / ".gitattributes").exists()
    before = contents(tmp_path)
    assert not config.initialize(core, project=project, host="codex")["applied"]
    assert contents(tmp_path) == before


def test_interrupted_area_registration_can_resume_without_losing_records(tmp_path, monkeypatch):
    original = core._atomic_write

    def interrupt_area(path, content):
        if path.name == "term.index.md":
            raise OSError("simulated interrupted seed")
        return original(path, content)

    with monkeypatch.context() as patch:
        patch.setattr(core, "_atomic_write", interrupt_area)
        with pytest.raises(OSError, match="simulated"):
            initialize(tmp_path, features=["term"], mode="auto")
    assert not (tmp_path / ".bobbin/config.json").exists()
    assert not (tmp_path / "context/term/term.index.md").exists()
    result = initialize(tmp_path, features=["term"], mode="auto")
    assert result["config"]["features"] == ["term"]
    assert core.doctor_repository(tmp_path)["repository_state"] == "ready"
