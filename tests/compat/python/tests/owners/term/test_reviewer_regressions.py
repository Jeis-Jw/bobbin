from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import term_test_support as helpers


term_cli = helpers.term_cli


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def public_result(completed: subprocess.CompletedProcess[str]) -> dict:
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)
    payload = json.loads(completed.stdout)
    if payload.get("ok") is not True or not isinstance(payload.get("result"), dict):
        raise AssertionError(completed.stdout + completed.stderr)
    return payload["result"]


def same_claim_attestation(value: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "same_claim",
        "input_schema": value["schema"],
        "input_digest": term_cli.canonical_digest(value),
        "assertions": [{
            "name": "same_semantic_claim",
            "value": True,
            "evidence_pointers": ["/predecessor/primary_claim", "/successor/primary_claim"],
        }],
    }


class TermReviewerRegressionTests(unittest.TestCase):
    def _initialized(self, root: Path, *, captured: bool = False) -> tuple[Path, list[str]]:
        repo = root / "repo"
        repo.mkdir()
        helpers.init_repo(repo)
        if captured:
            helpers.capture(repo)
        inventory, doctor = helpers.write_preflight(root)
        return repo, helpers.preflight_args(inventory, doctor)

    def _core(self, repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(helpers.CORE_CLI_PATH), *arguments],
            cwd=repo,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            capture_output=True,
        )

    def _assert_error_noop(
        self,
        repo: Path,
        completed: subprocess.CompletedProcess[str],
        before: str,
        code: str,
    ) -> dict:
        self.assertNotEqual(0, completed.returncode, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(code, payload["error"]["code"])
        self.assertNotIn("Traceback", completed.stdout + completed.stderr)
        self.assertEqual(before, helpers.tree_digest(repo))
        return payload

    def test_public_cross_artifact_vocabulary_intersections_fail_closed(self) -> None:
        cases = (
            ("primary-primary", "bff!", [], []),
            ("primary-alias", "Backend_for_Frontend", [], []),
            ("primary-deprecated", "API—Facade", [], []),
            ("alias-alias", "Gateway Alias", ["Backend—for—Frontend"], []),
            ("alias-deprecated", "Gateway Deprecated Alias", [], ["Backend_for_Frontend"]),
            ("deprecated-deprecated", "Gateway Deprecated", [], ["API—Facade"]),
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root, captured=True)
            before = helpers.tree_digest(repo)
            for index, (label, term, aliases, deprecated) in enumerate(cases, start=1):
                with self.subTest(label=label):
                    candidate = helpers.candidate(
                        candidate_id="cand_" + f"{index + 100:032x}",
                        term=term,
                        scope="project/auth/api",
                        title=f"Vocabulary collision {index}",
                    )
                    candidate["owner_inputs"]["term"].update({
                        "aliases": aliases,
                        "deprecated_terms": deprecated,
                        "related": [],
                    })
                    candidate_path = write_json(root / f"{label}-candidate.json", candidate)
                    proof_path = write_json(root / f"{label}-proof.json", helpers.attestation(candidate))
                    owner_result = public_result(helpers.run_cli(
                        repo,
                        "claim",
                        "--candidate", f"@{candidate_path}",
                        "--attestation", f"@{proof_path}",
                        "--identifier", f"ctx_550e8400e29b41d4a7164466554400{index:02x}",
                        "--created-at", "2026-08-22T03:00:00+09:00",
                        *preflight,
                        "--json",
                    ))
                    result_path = write_json(root / f"{label}-result.json", owner_result)
                    rejected = helpers.run_cli(
                        repo,
                        "batch",
                        "validate",
                        "--owner-result", f"@{result_path}",
                        *preflight,
                        "--json",
                    )
                    self._assert_error_noop(repo, rejected, before, "term_slot_conflict")

    def _exact_batch(self, target_bytes: int) -> dict:
        template = []
        for index in range(3):
            candidate = helpers.candidate(candidate_id="cand_" + f"{index + 1:032x}")
            candidate["source_refs"] = []
            template.append(candidate)
        for fixed_count in range(36):
            candidates = copy.deepcopy(template)
            for slot in range(fixed_count):
                candidates[slot // 12]["source_refs"].append(f"{slot:02d}-" + "x" * 497)
            tail_owner = candidates[fixed_count // 12]
            tail_owner["source_refs"].append("가")
            batch = {
                "schema": "context-capture-batch/v1",
                "audit_count": 1,
                "candidates": candidates,
            }
            delta = target_bytes - len(term_cli.canonical_json(batch).encode("utf-8"))
            if 0 <= delta <= 498:
                tail_owner["source_refs"][-1] += "x" * delta
                if len(term_cli.canonical_json(batch).encode("utf-8")) == target_bytes:
                    return batch
        self.fail(f"could not construct exact {target_bytes}-byte batch")

    def test_public_candidate_batch_caps_full_utf8_envelope_at_exact_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root)
            before = helpers.tree_digest(repo)
            exact = self._exact_batch(term_cli.MAX_CANDIDATE_BYTES)
            self.assertIn("가", term_cli.canonical_json(exact))
            exact_path = write_json(root / "batch-16384.json", exact)
            accepted = public_result(helpers.run_cli(
                repo,
                "candidate-batch",
                "validate",
                "--batch", f"@{exact_path}",
                *preflight,
                "--json",
            ))
            self.assertEqual(16384, accepted["canonical_bytes"])
            self.assertEqual(before, helpers.tree_digest(repo))

            over = copy.deepcopy(exact)
            over["candidates"][-1]["source_refs"][-1] += "x"
            self.assertEqual(16385, len(term_cli.canonical_json(over).encode("utf-8")))
            self.assertLessEqual(
                len(term_cli.canonical_json(over["candidates"]).encode("utf-8")),
                term_cli.MAX_CANDIDATE_BYTES,
            )
            over_path = write_json(root / "batch-16385.json", over)
            rejected = helpers.run_cli(
                repo,
                "candidate-batch",
                "validate",
                "--batch", f"@{over_path}",
                *preflight,
                "--json",
            )
            self._assert_error_noop(repo, rejected, before, "candidate_batch_too_large")

    def test_public_common_field_40_boundary_reaches_core_preview_and_41_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root)
            candidate = helpers.candidate()
            candidate["tags"] = ["t" * 40]
            candidate["search_terms"] = ["s" * 40]
            candidate_path = write_json(root / "common-40-candidate.json", candidate)
            proof_path = write_json(root / "common-40-proof.json", helpers.attestation(candidate))
            owner_result = public_result(helpers.run_cli(
                repo,
                "claim",
                "--candidate", f"@{candidate_path}",
                "--attestation", f"@{proof_path}",
                "--identifier", "ctx_550e8400e29b41d4a716446655440070",
                "--created-at", "2026-08-22T01:00:00+09:00",
                *preflight,
                "--json",
            ))
            result_path = write_json(root / "common-40-result.json", owner_result)
            receipt = public_result(helpers.run_cli(
                repo,
                "batch",
                "validate",
                "--owner-result", f"@{result_path}",
                *preflight,
                "--json",
            ))
            receipt_path = write_json(root / "common-40-receipt.json", receipt)
            before = helpers.tree_digest(repo)
            preview = public_result(self._core(
                repo,
                "transaction",
                "preview",
                "--owner-result", f"@{result_path}",
                "--owner-validation", f"@{receipt_path}",
                "--json",
            ))
            self.assertFalse(preview["applied"])
            self.assertEqual(before, helpers.tree_digest(repo))

            over = copy.deepcopy(candidate)
            over["tags"] = ["t" * 41]
            over_path = write_json(root / "common-41-candidate.json", over)
            over_proof = write_json(root / "common-41-proof.json", helpers.attestation(over))
            rejected = helpers.run_cli(
                repo,
                "claim",
                "--candidate", f"@{over_path}",
                "--attestation", f"@{over_proof}",
                "--route-only",
                *preflight,
                "--json",
            )
            self._assert_error_noop(repo, rejected, before, "schema_invalid")

            tampered = copy.deepcopy(owner_result)
            tampered["artifact_drafts"][0]["content"] = tampered["artifact_drafts"][0]["content"].replace(
                'tags: ["' + "t" * 40 + '"]',
                'tags: ["' + "t" * 41 + '"]',
            )
            tampered_path = write_json(root / "common-41-result.json", tampered)
            receipt_rejected = helpers.run_cli(
                repo,
                "batch",
                "validate",
                "--owner-result", f"@{tampered_path}",
                *preflight,
                "--json",
            )
            self._assert_error_noop(repo, receipt_rejected, before, "schema_invalid")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root, captured=True)
            before = helpers.tree_digest(repo)
            annotated = public_result(helpers.run_cli(
                repo,
                "annotate",
                "--id", "ctx_550e8400e29b41d4a716446655440000",
                "--tag", "a" * 40,
                "--updated-at", "2026-08-22T02:00:00+09:00",
                *preflight,
                "--json",
            ))
            annotated_path = write_json(root / "annotate-40-result.json", annotated)
            receipt = public_result(helpers.run_cli(
                repo,
                "batch",
                "validate",
                "--owner-result", f"@{annotated_path}",
                *preflight,
                "--json",
            ))
            receipt_path = write_json(root / "annotate-40-receipt.json", receipt)
            public_result(self._core(
                repo,
                "transaction",
                "preview",
                "--owner-result", f"@{annotated_path}",
                "--owner-validation", f"@{receipt_path}",
                "--json",
            ))
            self.assertEqual(before, helpers.tree_digest(repo))
            rejected = helpers.run_cli(
                repo,
                "annotate",
                "--id", "ctx_550e8400e29b41d4a716446655440000",
                "--tag", "a" * 41,
                "--updated-at", "2026-08-22T02:00:00+09:00",
                *preflight,
                "--json",
            )
            self._assert_error_noop(repo, rejected, before, "schema_invalid")

    def test_public_clock_invariants_reject_earlier_mutations_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root, captured=True)
            before = helpers.tree_digest(repo)
            identifier = "ctx_550e8400e29b41d4a716446655440000"
            commands: list[tuple[str, tuple[str, ...]]] = [
                ("annotate", (
                    "annotate", "--id", identifier, "--summary", "clock probe",
                    "--updated-at", "2026-08-22T00:59:59+09:00",
                )),
                ("deprecate", (
                    "deprecate", "--id", identifier, "--reason", "clock probe",
                    "--retired-at", "2026-08-22T00:59:59+09:00",
                )),
            ]
            successor = helpers.candidate(
                candidate_id="cand_550e8400e29b41d4a716446655440071",
                term="BFF!",
                definition="이 프로젝트에서 browser session, callback과 backend 인증 경계를 함께 소유하는 서비스다.",
                title="BFF clock successor",
            )
            successor_path = write_json(root / "clock-successor.json", successor)
            claim_proof = write_json(root / "clock-successor-proof.json", helpers.attestation(successor))
            same_input = public_result(helpers.run_cli(
                repo,
                "same-claim-input",
                "--id", identifier,
                "--successor-candidate", f"@{successor_path}",
                *preflight,
                "--json",
            ))
            same_path = write_json(root / "clock-same-input.json", same_input)
            same_proof = write_json(root / "clock-same-proof.json", same_claim_attestation(same_input))
            commands.append(("supersede", (
                "supersede", "--id", identifier,
                "--successor-candidate", f"@{successor_path}",
                "--claim-attestation", f"@{claim_proof}",
                "--same-claim-input", f"@{same_path}",
                "--same-claim-attestation", f"@{same_proof}",
                "--successor-id", "ctx_550e8400e29b41d4a716446655440071",
                "--retired-at", "2026-08-22T00:59:59+09:00",
            )))
            for label, command in commands:
                with self.subTest(label=label):
                    rejected = helpers.run_cli(repo, *command, *preflight, "--json")
                    self._assert_error_noop(repo, rejected, before, "clock_invalid")

            equal = public_result(helpers.run_cli(
                repo,
                "annotate",
                "--id", identifier,
                "--summary", "created_at equality is allowed",
                "--updated-at", "2026-08-22T01:00:00+09:00",
                *preflight,
                "--json",
            ))
            equal_path = write_json(root / "clock-equal-result.json", equal)
            self.assertEqual(
                "valid",
                public_result(helpers.run_cli(
                    repo,
                    "batch",
                    "validate",
                    "--owner-result", f"@{equal_path}",
                    *preflight,
                    "--json",
                ))["status"],
            )
            self.assertEqual(before, helpers.tree_digest(repo))

    def test_public_malformed_attestations_return_closed_json_errors_byte_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, preflight = self._initialized(root)
            candidate = helpers.candidate()
            candidate_path = write_json(root / "attestation-candidate.json", candidate)
            valid = helpers.attestation(candidate)
            cases = {
                "scalar": 7,
                "assertion-scalar": {**valid, "assertions": [True, valid["assertions"][1]]},
                "envelope-extra": {**valid, "unknown": True},
                "assertion-extra": {
                    **valid,
                    "assertions": [{**valid["assertions"][0], "unknown": True}, valid["assertions"][1]],
                },
                "pointer-scalar": {
                    **valid,
                    "assertions": [
                        {**valid["assertions"][0], "evidence_pointers": "/owner_inputs/term/term"},
                        valid["assertions"][1],
                    ],
                },
            }
            before = helpers.tree_digest(repo)
            for label, attestation in cases.items():
                with self.subTest(label=label):
                    proof_path = write_json(root / f"attestation-{label}.json", attestation)
                    rejected = helpers.run_cli(
                        repo,
                        "claim",
                        "--candidate", f"@{candidate_path}",
                        "--attestation", f"@{proof_path}",
                        "--route-only",
                        *preflight,
                        "--json",
                    )
                    self.assertEqual(5, rejected.returncode, rejected.stdout + rejected.stderr)
                    self._assert_error_noop(repo, rejected, before, "semantic_attestation_invalid")


if __name__ == "__main__":
    unittest.main()
