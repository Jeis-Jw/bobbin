from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
CORE = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
WORKFLOW = ROOT / "plugins/bobbin/skills/decision/scripts/decision_workflow.py"
OWNERS = ("decision", "assumption", "term", "intent", "document")


@pytest.fixture
def no_git(tmp_path):
    empty_bin = tmp_path / "empty-bin"
    private_temp = tmp_path / "private-temp"
    empty_bin.mkdir()
    private_temp.mkdir(mode=0o700)
    assert shutil.which("git", path=str(empty_bin)) is None
    return {
        **os.environ,
        "PATH": str(empty_bin),
        "TMPDIR": str(private_temp),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_DIR": str(tmp_path / "missing-metadata"),
        "GIT_WORK_TREE": str(tmp_path / "wrong-root"),
    }


def invoke(script, cwd, environment, *arguments, expected=0):
    completed = subprocess.run(
        [sys.executable, str(script), *map(str, arguments), "--json"],
        cwd=cwd, env=environment, text=True, capture_output=True,
    )
    assert completed.returncode == expected, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is (expected == 0)
    return payload["result"] if expected == 0 else payload["error"]


def files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def owner_init(owner):
    return ROOT / f"plugins/bobbin/skills/init/scripts/{owner}_init.py"


def test_core_discovers_nearest_vault_and_honors_explicit_selection(tmp_path, no_git):
    vault = tmp_path / "ordinary folder"
    nested = vault / "work/nested"
    nested.mkdir(parents=True)
    (tmp_path / ".git").write_text("gitdir: nonexistent\n")
    invoke(CORE, vault, no_git, "init", "--host", "codex")
    before = files(vault)
    assert invoke(CORE, nested, no_git, "doctor")["repository_state"] == "ready"
    invoke(CORE, nested, no_git, "init", "--host", "codex")
    assert files(vault) == before

    independent = nested / "independent"
    independent.mkdir()
    invoke(CORE, nested, no_git, "--vault", "independent", "init", "--host", "codex")
    assert (independent / "context/context.index.md").is_file()
    assert invoke(CORE, independent, no_git, "doctor")["repository_state"] == "ready"

    for bad_path in (tmp_path / "missing", vault / "AGENTS.md"):
        error = invoke(CORE, nested, no_git, "--vault", bad_path, "doctor", expected=3)
        assert error["code"] == "vault_not_found"
    invoke(CORE, nested, no_git, "--vault", tmp_path / "missing", "schema")

    # A broken nearest context must not silently select the healthy parent vault.
    (nested / "context").symlink_to(tmp_path / "missing-context", target_is_directory=True)
    assert invoke(CORE, nested, no_git, "doctor")["repository_state"] != "ready"
    assert invoke(CORE, nested, no_git, "--vault", vault, "doctor")["repository_state"] == "ready"


@pytest.mark.parametrize("owner", OWNERS)
def test_addon_init_and_reads_select_explicit_vault_from_another_project(tmp_path, no_git, owner):
    caller, vault = tmp_path / "caller", tmp_path / "별도 vault"
    caller.mkdir()
    vault.mkdir()
    invoke(CORE, caller, no_git, "init", "--host", "codex")
    before = files(caller)
    arguments = ("--vault", vault, "--host", "codex", "--core-cli", CORE)
    invoke(owner_init(owner), caller, no_git, *arguments)
    assert (vault / f"context/{owner}/{owner}.index.md").is_file()
    initialized = files(vault)
    invoke(owner_init(owner), caller, no_git, *arguments)
    assert files(vault) == initialized
    assert files(caller) == before

    preflight = []
    if owner != "decision":
        inventory = {"plugins": [{"marketplace": "bobbin", "plugin": "bobbin", "source": "Jeis-Jw/bobbin", "enabled": True, "protocols": ["context-common/v2"], "entrypoint": str(CORE)}]}
        (caller / "inventory.json").write_text(json.dumps(inventory))
        (caller / "doctor.json").write_text(json.dumps(invoke(CORE, caller, no_git, "--vault", vault, "doctor")))
        preflight = ["--host", "codex", "--core-inventory", "@inventory.json", "--core-doctor", "@doctor.json"]
        signal = {"assumption": "assumption-relevant", "term": "term-encountered"}.get(owner)
        if signal:
            preflight = ["--signal", signal, *preflight]
    cli = ROOT / f"plugins/bobbin/skills/{owner}/scripts/{owner}_cli.py"
    invoke(cli, caller, no_git, "--vault", vault, "search", "--query", "absent fixture", *preflight)
    missing = invoke(
        cli,
        caller,
        no_git,
        "--vault",
        vault,
        "read",
        "--id",
        "ctx_550e8400e29b41d4a716446655440000",
        *preflight,
        expected=3,
    )
    assert missing["code"] == "artifact_not_found"
    nested = vault / "nested"
    nested.mkdir()
    if preflight:
        shutil.copy(caller / "inventory.json", nested)
        shutil.copy(caller / "doctor.json", nested)
    invoke(cli, nested, no_git, "search", "--query", "absent fixture", *preflight)
    error = invoke(cli, caller, no_git, "--vault", tmp_path / "missing", "search", "--query", "fixture", *preflight, expected=3)
    assert error["code"] == "vault_not_found"


@pytest.mark.parametrize("kind", ("observation", "snapshot", "decision"))
def test_capture_uses_caller_inputs_and_vault_approval_without_git(tmp_path, no_git, kind):
    caller, vault = tmp_path / "caller", tmp_path / "standalone vault"
    caller.mkdir()
    vault.mkdir()
    (caller / "body.txt").write_text("Use Markdown files for durable project context.")
    (vault / ".git").write_text("gitdir: ignored-old-metadata\n")
    invoke(owner_init("decision"), caller, no_git, "--vault", vault, "--host", "codex", "--core-cli", CORE)
    shared = ["--title", "Vault portability", "--summary", "Exercise ordinary directories.", "--captured-from", "conversation"]
    if kind == "observation":
        script, command = CORE, ["observation", "capture"]
        capture = ["--sec-observation", "@body.txt", "--sec-evidence", "Observed in this fixture.", "--attest-reusable-observation", "--attest-evidence-present"]
    elif kind == "snapshot":
        script, command = CORE, ["snapshot", "save"]
        capture = ["--sec-context", "@body.txt", "--sec-open-items", "Verify remaining flows.", "--sec-next-steps", "Run the regression suite.", "--attest-handoff-requested", "--attest-unfinished-context-present"]
    else:
        script, command = WORKFLOW, ["preview", "--host", "codex", "--core-cli", CORE, "--inline"]
        capture = ["--scope", "project/vault", "--decision-key", "storage-choice", "--sec-decision", "@body.txt", "--sec-rationale", "Keep context portable.", "--sec-alternatives", "A database was rejected because files meet this fixture's requirements.", "--commitment-evidence", "The fixture owner explicitly chose Markdown.", "--attest-explicit-choice", "--attest-scope-identified", "--attest-commitment-present"]
    before = files(vault)
    preview = invoke(script, caller, no_git, "--vault", vault, *command, *shared, *capture)
    assert files(vault) == before
    receipt_path = Path(preview["receipt_file"])
    apply_command = ["apply", "--core-cli", CORE] if kind == "decision" else ["transaction", "apply"]
    apply_arguments = [*apply_command, "--receipt-file", receipt_path, "--approved-digest", preview["approval_digest"]]

    copied = tmp_path / "copied vault"
    shutil.copytree(vault, copied)
    copied_before = files(copied)
    error = invoke(script, caller, no_git, "--vault", copied, *apply_arguments, expected=5)
    assert error["code"] == "vault_identity_mismatch"
    assert files(copied) == copied_before

    # Optional version-control changes do not invalidate an otherwise unchanged preview.
    (vault / ".git").unlink()
    (vault / ".git").mkdir()
    (vault / ".git/HEAD").write_text("arbitrary metadata, never inspected\n")
    before = files(vault)
    wrong = [*apply_arguments[:-1], "sha256:" + "0" * 64]
    invoke(script, caller, no_git, "--vault", vault, *wrong, expected=5)
    assert files(vault) == before
    assert invoke(script, caller, no_git, "--vault", vault, *apply_arguments)["applied"]
    assert not receipt_path.exists()
    artifacts = [path for path in (vault / f"context/{kind}").glob("*.md") if not path.name.endswith(".index.md")]
    assert len(artifacts) == 1
    assert "Use Markdown files for durable project context." in artifacts[0].read_text()
    invoke(CORE, caller, no_git, "--vault", vault, "recall", "--query", "Vault portability")
    assert not (caller / "context").exists()


@pytest.mark.parametrize("owner", OWNERS)
def test_addon_rejects_core_without_vault_capability_before_storage(tmp_path, no_git, owner):
    plugin = tmp_path / "older/context-core"
    shutil.copytree(ROOT / "plugins/bobbin", plugin, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    old_core = plugin / "skills/context/scripts/context_cli.py"
    old_core.write_text(old_core.read_text().replace(', "filesystem-vault/v1"', ''))
    vault = tmp_path / "vault"
    vault.mkdir()
    error = invoke(owner_init(owner), tmp_path, no_git, "--vault", vault, "--host", "codex", "--core-cli", old_core, expected=5)
    assert error["code"] == "core_incompatible"
    assert files(vault) == {}
