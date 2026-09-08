from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest
import uuid
from pathlib import Path

import assumption_test_support as helpers


assumption_cli = helpers.assumption_cli


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def same_claim_attestation(value: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "same_claim",
        "input_schema": value["schema"],
        "input_digest": assumption_cli.canonical_digest(value),
        "assertions": [{"name": "same_semantic_claim", "value": True, "evidence_pointers": ["/predecessor/primary_claim", "/successor/primary_claim"]}],
    }


def exact_candidate_batch(target_bytes: int) -> dict:
    template = []
    for index in range(3):
        value = helpers.candidate(candidate_id="cand_" + f"{index + 1:032x}")
        value["source_refs"] = []
        template.append(value)
    for fixed_count in range(36):
        candidates = copy.deepcopy(template)
        for slot in range(fixed_count):
            candidates[slot // 12]["source_refs"].append(f"{slot:02d}-" + "x" * 497)
        tail = candidates[fixed_count // 12]["source_refs"]
        tail.append("가")
        batch = {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": candidates}
        delta = target_bytes - len(assumption_cli.canonical_json(batch).encode("utf-8"))
        if 0 <= delta <= 498:
            tail[-1] += "x" * delta
            if len(assumption_cli.canonical_json(batch).encode("utf-8")) == target_bytes:
                return batch
    raise AssertionError(f"could not construct exact {target_bytes}-byte batch")


class AssumptionSecurityRegressionTests(unittest.TestCase):
    def _initialized(self, root: Path, *, captured: bool = True) -> tuple[Path, list[str]]:
        repo = root / "repo"
        repo.mkdir()
        helpers.init_repo(repo)
        if captured:
            helpers.capture(repo)
        inventory, doctor = helpers.write_preflight(root)
        return repo, helpers.preflight_args(inventory, doctor)

    def _public_validate(self, repo: Path, preflight: list[str], root: Path, result: dict, name: str) -> subprocess.CompletedProcess[str]:
        result_path = write_json(root / f"{name}.json", result)
        return helpers.run_cli(repo, "batch", "validate", "--owner-result", f"@{result_path}", *preflight, "--json")

    def _assert_rejected_noop(self, repo: Path, completed: subprocess.CompletedProcess[str], before: str) -> dict:
        self.assertEqual(5, completed.returncode, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertNotIn("result", payload)
        self.assertEqual(before, helpers.tree_digest(repo))
        return payload

    def test_public_candidate_batch_uses_full_utf8_envelope_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root, captured=False)
            before = helpers.tree_digest(repo)
            exact = exact_candidate_batch(assumption_cli.MAX_CANDIDATE_BYTES)
            self.assertIn("가", assumption_cli.canonical_json(exact))
            self.assertLess(
                len(assumption_cli.canonical_json(exact["candidates"]).encode("utf-8")),
                assumption_cli.MAX_CANDIDATE_BYTES,
            )
            exact_path = write_json(root / "batch-16384.json", exact)
            accepted = helpers.run_cli(repo, "candidate-batch", "validate", "--batch", f"@{exact_path}", *preflight, "--json")
            self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
            self.assertEqual(16384, json.loads(accepted.stdout)["result"]["canonical_bytes"])

            over = copy.deepcopy(exact)
            over["candidates"][-1]["source_refs"][-1] += "x"
            self.assertEqual(16385, len(assumption_cli.canonical_json(over).encode("utf-8")))
            self.assertLessEqual(
                len(assumption_cli.canonical_json(over["candidates"]).encode("utf-8")),
                assumption_cli.MAX_CANDIDATE_BYTES,
            )
            over_path = write_json(root / "batch-16385.json", over)
            rejected = helpers.run_cli(repo, "candidate-batch", "validate", "--batch", f"@{over_path}", *preflight, "--json")
            payload = self._assert_rejected_noop(repo, rejected, before)
            self.assertEqual("candidate_batch_too_large", payload["error"]["code"])

            eight = {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": [helpers.candidate(candidate_id="cand_" + f"{index + 1:032x}") for index in range(8)]}
            eight_path = write_json(root / "batch-eight.json", eight)
            self.assertEqual(0, helpers.run_cli(repo, "candidate-batch", "validate", "--batch", f"@{eight_path}", *preflight, "--json").returncode)
            nine = {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": [helpers.candidate(candidate_id="cand_" + f"{index + 1:032x}") for index in range(9)]}
            nine_path = write_json(root / "batch-nine.json", nine)
            count_rejected = helpers.run_cli(repo, "candidate-batch", "validate", "--batch", f"@{nine_path}", *preflight, "--json")
            count_payload = self._assert_rejected_noop(repo, count_rejected, before)
            self.assertEqual("candidate_batch_too_large", count_payload["error"]["code"])

    def test_public_receipt_rejects_detached_claim_and_semantic_annotate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root, captured=False)
            value = helpers.candidate()
            detached = assumption_cli.build_claim_result(value, helpers.attestation(value), identifier="ctx_550e8400e29b41d4a716446655440000", created_at="2026-08-22T01:00:00+09:00")
            unrelated = copy.deepcopy(value)
            unrelated["claim"] = unrelated["owner_inputs"]["assumption"]["assumption"] = "이 결과와 무관한 별도 가정이다."
            detached["semantic_inputs"][0] = assumption_cli._semantic_input("claim", unrelated)
            detached["semantic_attestations"][0] = helpers.attestation(unrelated)
            before = helpers.tree_digest(repo)
            payload = self._assert_rejected_noop(repo, self._public_validate(repo, preflight, root, detached, "detached"), before)
            self.assertEqual("owner_result_rederivation_mismatch", payload["error"]["code"])

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root)
            changed = assumption_cli.build_annotate_result(repo, "ctx_550e8400e29b41d4a716446655440000", summary="metadata annotation", updated_at="2026-08-22T02:00:00+09:00")
            draft = changed["artifact_drafts"][0]
            frontmatter, sections = assumption_cli.parse_document(draft["content"])
            sections["Assumption"] = "annotation으로 몰래 바꾼 semantic claim"
            draft["content"] = assumption_cli.render_document(frontmatter, sections)
            draft["semantic_projection"]["primary_claim"] = sections["Assumption"]
            before = helpers.tree_digest(repo)
            payload = self._assert_rejected_noop(repo, self._public_validate(repo, preflight, root, changed, "semantic-annotate"), before)
            self.assertEqual("owner_result_rederivation_mismatch", payload["error"]["code"])

    def test_public_receipt_rejects_supersede_and_refute_binding_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root)
            successor = helpers.candidate(candidate_id="cand_550e8400e29b41d4a716446655440001", assumption="정상 네트워크에서는 IdP callback이 5초 안에 반환할 것이다.", title="정상 네트워크 callback 전제")
            same = assumption_cli.prepare_same_claim_input(repo, "ctx_550e8400e29b41d4a716446655440000", successor)
            result = assumption_cli.build_supersede_result(repo, "ctx_550e8400e29b41d4a716446655440000", successor, helpers.attestation(successor), same, same_claim_attestation(same), successor_id="ctx_550e8400e29b41d4a716446655440001", retired_at="2026-08-22T02:00:00+09:00")
            inputs = {item["operation"]: item for item in result["semantic_inputs"]}
            altered_same = copy.deepcopy(inputs["same_claim"]["value"])
            altered_same["successor"]["primary_claim"] = "successor artifact와 다른 claim"
            inputs["same_claim"].update({"value": altered_same, "input_digest": assumption_cli.canonical_digest(altered_same)})
            for index, item in enumerate(result["semantic_attestations"]):
                if item["operation"] == "same_claim":
                    result["semantic_attestations"][index] = same_claim_attestation(altered_same)
            request = inputs["mutation_request"]["value"]
            request["requested_changes"]["same_claim_input_digest"] = assumption_cli.canonical_digest(altered_same)
            inputs["mutation_request"]["input_digest"] = assumption_cli.canonical_digest(request)
            before = helpers.tree_digest(repo)
            payload = self._assert_rejected_noop(repo, self._public_validate(repo, preflight, root, result, "supersede-detached"), before)
            self.assertIn(payload["error"]["code"], {"same_claim_input_invalid", "owner_result_rederivation_mismatch"})

        for attack in ("request_draft_mismatch", "decision_mutation"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo, preflight = self._initialized(root)
                result = assumption_cli.build_refute_result(repo, "ctx_550e8400e29b41d4a716446655440000", "production p95가 8초였다.", ["metrics:p95"], [], retired_at="2026-08-22T02:00:00+09:00")
                request_item = next(item for item in result["semantic_inputs"] if item["operation"] == "mutation_request")
                if attack == "request_draft_mismatch":
                    request_item["value"]["requested_changes"]["reason"] = "draft와 다른 공격자 reason"
                else:
                    request_item["value"]["requested_changes"]["decision_mutation"] = True
                request_item["input_digest"] = assumption_cli.canonical_digest(request_item["value"])
                before = helpers.tree_digest(repo)
                payload = self._assert_rejected_noop(repo, self._public_validate(repo, preflight, root, result, attack), before)
                self.assertIn(payload["error"]["code"], {"mutation_request_invalid", "owner_result_rederivation_mismatch"})

    def test_public_read_rejects_absolute_parent_and_symlink_paths_without_exfiltration(self) -> None:
        for attack in ("absolute", "parent", "symlink"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo, preflight = self._initialized(root)
                secret = root / "external-secret.txt"
                secret.write_text("EXFILTRATION_SENTINEL", encoding="utf-8")
                index_path = repo / assumption_cli.ASSUMPTION_INDEX
                index_text = index_path.read_text(encoding="utf-8")
                _, current, _ = assumption_cli._index(repo)
                artifact = repo / current[0]["path"]
                if attack in {"absolute", "parent"}:
                    replacement = str(secret) if attack == "absolute" else "context/assumption/../../../../external-secret.txt"
                    lines = []
                    for line in index_text.splitlines():
                        match = assumption_cli.ENTRY_RE.fullmatch(line)
                        if match:
                            row = json.loads(match.group(1))
                            row["path"] = replacement
                            line = line.split("<!-- context-entry ", 1)[0] + "<!-- context-entry " + json.dumps(row, ensure_ascii=False, separators=(",", ":")) + " -->"
                        lines.append(line)
                    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                else:
                    artifact.unlink()
                    artifact.symlink_to(secret)
                before = helpers.tree_digest(repo)
                completed = helpers.run_cli(repo, "read", "--signal", "assumption-relevant", "--id", "ctx_550e8400e29b41d4a716446655440000", *preflight, "--json")
                payload = self._assert_rejected_noop(repo, completed, before)
                self.assertIn(payload["error"]["code"], {"path_escape", "symlink_path"})
                self.assertNotIn("EXFILTRATION_SENTINEL", completed.stdout + completed.stderr)

    def test_public_init_binds_active_core_and_checks_registration_postcondition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            fake = root / "context_cli.py"
            marker = root / "executed"
            fake.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n", encoding="utf-8")
            before = helpers.tree_digest(repo)
            completed = subprocess.run([sys.executable, str(helpers.INIT_PATH), "--host", "codex", "--core-cli", str(fake), "--json"], cwd=repo, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, text=True, capture_output=True)
            payload = self._assert_rejected_noop(repo, completed, before)
            self.assertEqual("core_surface_mismatch", payload["error"]["code"])
            self.assertFalse(marker.exists())

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            fake = root / "plugins/bobbin/skills/context/scripts/context_cli.py"
            fake.parent.mkdir(parents=True)
            fake.write_text(
                "import json,sys\n"
                "command=sys.argv[1]\n"
                "if command=='schema': result={'schema':'context-core-schema/v1','protocol':'context-common/v2','features':['context-owner-descriptor/v2']}\n"
                "elif command=='bootstrap': result={'changed_paths':[],'phases':[],'doctor':{},'policy':{}}\n"
                "elif command=='doctor': result={'schema':'context-core-doctor/v1','owner':'context-core','repository_state':'ready','issues':[]}\n"
                "else: raise SystemExit(2)\n"
                "print(json.dumps({'ok':True,'result':result}))\n",
                encoding="utf-8",
            )
            before = helpers.tree_digest(repo)
            completed = subprocess.run([sys.executable, str(helpers.INIT_PATH), "--host", "codex", "--core-cli", str(fake), "--json"], cwd=repo, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, text=True, capture_output=True)
            payload = self._assert_rejected_noop(repo, completed, before)
            self.assertEqual("core_surface_mismatch", payload["error"]["code"])

    def test_public_bounds_closed_fields_mixed_decline_and_doctor_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            inventory, doctor = helpers.write_preflight(root)
            preflight = helpers.preflight_args(inventory, doctor)
            value = helpers.candidate()
            before = helpers.tree_digest(repo)
            for label, offered in (("offered", True), ("structured-only", False)):
                mixed = copy.deepcopy(value)
                mixed["requested_kind"] = None if offered else "assumption"
                mixed["specialized_kinds"] = ["assumption", "decision"] if offered else ["assumption"]
                mixed["owner_inputs"]["decision"] = {"decision": "현재 이 선택을 따른다."}
                mixed_path = write_json(root / f"mixed-{label}.json", mixed)
                proof_path = write_json(root / f"mixed-{label}-attestation.json", helpers.attestation(mixed))
                completed = helpers.run_cli(repo, "claim", "--candidate", f"@{mixed_path}", "--attestation", f"@{proof_path}", "--route-only", *preflight, "--json")
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                self.assertEqual("decline", json.loads(completed.stdout)["result"]["decision"])
                self.assertEqual(before, helpers.tree_digest(repo))

            attacks = []
            extra = copy.deepcopy(value)
            extra["unknown"] = True
            attacks.append(("closed", extra, "candidate_invalid"))
            owner_large = copy.deepcopy(value)
            owner_large["owner_inputs"]["assumption"]["basis"] = ["가" * 500] * 4
            owner_large["owner_inputs"]["assumption"]["confirm_conditions"] = ["나" * 300] * 8
            attacks.append(("owner-large", owner_large, "owner_input_too_large"))
            candidate_large = copy.deepcopy(value)
            candidate_large["title"] = "x" * 17000
            attacks.append(("candidate-large", candidate_large, "candidate_too_large"))
            for name, attacked, code in attacks:
                candidate_path = write_json(root / f"{name}.json", attacked)
                proof = write_json(root / f"{name}-proof.json", helpers.attestation(attacked))
                failed = helpers.run_cli(repo, "claim", "--candidate", f"@{candidate_path}", "--attestation", f"@{proof}", "--route-only", *preflight, "--json")
                self.assertEqual(5, failed.returncode, failed.stdout + failed.stderr)
                self.assertEqual(code, json.loads(failed.stdout)["error"]["code"])
                self.assertEqual(before, helpers.tree_digest(repo))

            candidates = []
            for index in range(8):
                item = helpers.candidate(candidate_id="cand_" + f"{index + 1:032x}")
                item["owner_inputs"]["assumption"]["basis"] = [(chr(65 + index) + "x" * 340) for _ in range(4)]
                candidates.append(item)
            batch = write_json(root / "batch.json", {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": candidates})
            failed_batch = helpers.run_cli(repo, "candidate-batch", "validate", "--batch", f"@{batch}", *preflight, "--json")
            self.assertEqual(5, failed_batch.returncode, failed_batch.stdout + failed_batch.stderr)
            self.assertEqual("candidate_batch_too_large", json.loads(failed_batch.stdout)["error"]["code"])

            candidate_path = write_json(root / "ready-candidate.json", value)
            proof_path = write_json(root / "ready-proof.json", helpers.attestation(value))
            for state, expected in (("partial", "core_partial"), ("invalid", "core_invalid")):
                state_root = root / state
                state_root.mkdir()
                inventory_state, doctor_state = helpers.write_preflight(state_root, state)
                failed = helpers.run_cli(repo, "claim", "--candidate", f"@{candidate_path}", "--attestation", f"@{proof_path}", "--route-only", *helpers.preflight_args(inventory_state, doctor_state), "--json")
                self.assertEqual(5, failed.returncode, failed.stdout + failed.stderr)
                self.assertEqual(expected, json.loads(failed.stdout)["error"]["code"])
            partial_root = root / "init-partial"
            partial_root.mkdir()
            partial_inventory, partial_doctor = helpers.write_preflight(partial_root, "partial")
            init_plan = helpers.run_cli(repo, "init", *helpers.preflight_args(partial_inventory, partial_doctor), "--json")
            self.assertEqual(0, init_plan.returncode, init_plan.stdout + init_plan.stderr)

    def test_public_output_is_bounded_by_actual_canonical_utf8_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root, captured=False)
            index_path = repo / assumption_cli.ASSUMPTION_INDEX
            text = index_path.read_text(encoding="utf-8")
            rows = []
            nfd = unicodedata.normalize("NFD", "가") * 700
            for offset in range(50):
                identifier = "ctx_" + uuid.uuid4().hex
                path = f"context/assumption/item-{offset}.md"
                row = {"id": identifier, "path": path, "title": f"item {offset}", "summary": nfd, "state": "current", "created_at": "2026-08-22T01:00:00+09:00", "terms": [nfd], "scope": "project/test"}
                rows.append(f"- [[{path[:-3]}]] — item <!-- context-entry {json.dumps(row, ensure_ascii=False, separators=(',', ':'))} -->")
            text = text.replace("<!-- BEGIN CONTEXT GENERATED:current -->\n<!-- END CONTEXT GENERATED:current -->", "<!-- BEGIN CONTEXT GENERATED:current -->\n" + "\n".join(rows) + "\n<!-- END CONTEXT GENERATED:current -->")
            index_path.write_text(text, encoding="utf-8")
            before = helpers.tree_digest(repo)
            completed = helpers.run_cli(repo, "search", "--signal", "assumption-relevant", "--query", "", "--limit", "50", *preflight, "--json")
            self.assertEqual(5, completed.returncode, completed.stdout + completed.stderr)
            self.assertEqual("output_too_large", json.loads(completed.stdout)["error"]["code"])
            self.assertLessEqual(len(completed.stdout.encode("utf-8")), assumption_cli.MAX_PUBLIC_OUTPUT_BYTES)
            self.assertEqual(before, helpers.tree_digest(repo))

    def test_public_operations_reject_malformed_core_doctor_receipts_byte_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, _ = self._initialized(root)
            malformed_root = root / "malformed"
            malformed_root.mkdir()
            inventory, doctor_path = helpers.write_preflight(malformed_root)
            value = helpers.candidate()
            candidate_path = write_json(root / "doctor-candidate.json", value)
            proof_path = write_json(root / "doctor-proof.json", helpers.attestation(value))
            result = assumption_cli.build_annotate_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                summary="doctor attack fixture",
                updated_at="2026-08-22T02:00:00+09:00",
            )
            result_path = write_json(root / "doctor-result.json", result)
            base = {
                "schema": "context-core-doctor/v1",
                "owner": "context-core",
                "supported_protocols": ["context-common/v2"],
                "repository_state": "ready",
                "root": "context/",
                "issues": [],
                "warnings": [],
                "plugin_version": "1.0.0",
                "entrypoint": str(helpers.CORE_CLI_PATH.resolve()),
                "protocol": "context-common/v2",
            }
            cases = []
            wrong_schema = dict(base, schema="context-core-doctor/v0")
            cases.append(("wrong-schema", wrong_schema, ("claim", "--candidate", f"@{candidate_path}", "--attestation", f"@{proof_path}", "--route-only")))
            wrong_owner = dict(base, owner="context-assumption")
            cases.append(("wrong-owner", wrong_owner, ("read", "--signal", "assumption-relevant", "--id", "ctx_550e8400e29b41d4a716446655440000")))
            missing = dict(base)
            missing.pop("warnings")
            cases.append(("missing-field", missing, ("batch", "validate", "--owner-result", f"@{result_path}")))
            wrong_self_report = dict(base, protocol="context-common/v1")
            cases.append(("wrong-self-report", wrong_self_report, ("read", "--signal", "assumption-relevant", "--id", "ctx_550e8400e29b41d4a716446655440000")))
            ready_issues = dict(base, issues=[{"code": "owner_profile_mismatch", "path": "context/context.index.md"}])
            cases.append(("ready-issues", ready_issues, ("batch", "validate", "--owner-result", f"@{result_path}")))
            before = helpers.tree_digest(repo)
            for label, doctor, command in cases:
                with self.subTest(label=label):
                    doctor_path.write_text(json.dumps(doctor), encoding="utf-8")
                    completed = helpers.run_cli(repo, *command, *helpers.preflight_args(inventory, doctor_path), "--json")
                    payload = self._assert_rejected_noop(repo, completed, before)
                    self.assertEqual("core_preflight_invalid", payload["error"]["code"])


if __name__ == "__main__":
    unittest.main()
