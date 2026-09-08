#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file()) / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/context/scripts/context_cli.py"
SPEC = importlib.util.spec_from_file_location("context_cli_routing", CLI_PATH)
assert SPEC and SPEC.loader
context_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = context_cli
SPEC.loader.exec_module(context_cli)


def candidate(candidate_id: str, *, requested: str | None = None, specialized: list[str] | None = None, fallback: str | None = "observation") -> dict:
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": candidate_id,
        "title": "인증 세션 소유권",
        "claim": "인증 세션은 BFF가 소유한다.",
        "summary": "인증 세션 경계를 BFF로 통합한다.",
        "captured_from": "conversation",
        "requested_kind": requested,
        "specialized_kinds": specialized or ["decision"],
        "fallback_kind": fallback,
        "scope_hint": "project/auth",
        "evidence": ["결정 권한자가 현재 따를 선택으로 확정했다."],
        "owner_inputs": {
            "decision": {
                "decision": "인증 세션은 BFF가 소유한다.",
                "rationale": "cookie 경계를 서버로 모은다.",
                "rejected_alternatives": ["SPA token 소유: 노출 범위가 커져 반려"],
                "decision_key": "session-owner",
            },
            "observation": {
                "observation": "인증 세션은 BFF가 소유한다.",
                "evidence": ["결정 권한자가 현재 따를 선택으로 확정했다."],
            },
        },
    }


def result(value: dict, capability: dict, decision: str, *, owner: str | None = None, kind: str | None = None) -> dict:
    return {
        "schema": "context-owner-result/v1",
        "result_type": "claim",
        "transition": "capture",
        "owner": owner or capability["owner"],
        "target_kind": kind or capability["kind"],
        "candidate_id": value["candidate_id"],
        "decision": decision,
        "reason": "fixture result",
        "capability_digest": context_cli.canonical_digest(capability),
        "semantic_inputs": [{
            "operation": "claim",
            "input_schema": value["schema"],
            "input_digest": context_cli.canonical_digest(value),
            "value": value,
        }],
        "semantic_attestations": [],
        "artifact_drafts": [],
        "effects": [],
        "proposed_plan": None,
    }


def decision_capability() -> dict:
    return {
        "schema": "context-owner-capability/v1",
        "owner": "context-decision",
        "kind": "decision",
        "artifact_schema": "context-decision/v1",
        "authority": "authoritative",
        "claim_surface": {"type": "agent_skill", "name": "context-decision:decision", "operation": "claim"},
        "batch_validation_surface": {"type": "cli", "command": "decision_cli.py batch validate"},
        "claim_rule": "accepted choice",
        "claim_assertions": ["explicit_choice", "scope_identified", "commitment_present"],
        "lifecycle_operations": {"same_claim": {"surface": {"type": "agent_skill", "name": "context-decision:decision", "operation": "same_claim"}, "rule": "same claim", "assertions": ["same_semantic_claim"]}},
        "draft_fields": {
            "required": {
                "decision": {"type": "string", "min_chars": 1, "max_chars": 1200},
                "rationale": {"type": "string", "min_chars": 1, "max_chars": 1200},
                "rejected_alternatives": {"type": "string_list", "min_items": 1, "max_items": 8, "max_item_chars": 500},
                "decision_key": {"type": "string", "min_chars": 1, "max_chars": 80},
            },
            "optional": {},
        },
    }


def exact_candidate_batch(target_bytes: int) -> dict:
    template = []
    for index in range(3):
        value = candidate("cand_" + f"{index + 1:032x}")
        value["source_refs"] = []
        template.append(value)
    for fixed_count in range(36):
        values = copy.deepcopy(template)
        for slot in range(fixed_count):
            values[slot // 12]["source_refs"].append(f"{slot:02d}-" + "x" * 497)
        tail = values[fixed_count // 12]["source_refs"]
        tail.append("가")
        batch = {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": values}
        delta = target_bytes - len(context_cli.canonical_json(batch).encode("utf-8"))
        if 0 <= delta <= 498:
            tail[-1] += "x" * delta
            if len(context_cli.canonical_json(batch).encode("utf-8")) == target_bytes:
                return batch
    raise AssertionError(f"could not construct exact {target_bytes}-byte batch")


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def public_route(repo: Path, root: Path, batch: object) -> subprocess.CompletedProcess[str]:
    batch_path = write_json(root / "batch.json", batch)
    capabilities_path = write_json(root / "capabilities.json", context_cli.capabilities_result())
    results_path = write_json(root / "claim-results.json", {"schema": "context-owner-results/v1", "results": []})
    return subprocess.run(
        [
            sys.executable,
            str(CLI_PATH),
            "candidate",
            "route",
            "--batch",
            f"@{batch_path}",
            "--capabilities",
            f"@{capabilities_path}",
            "--claim-results",
            f"@{results_path}",
            "--json",
        ],
        cwd=repo,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
    )


def repository_bytes(repo: Path) -> dict[str, bytes]:
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in sorted(repo.rglob("*"))
        if path.is_file()
    }


class RoutingRecallTests(unittest.TestCase):
    def test_public_candidate_batch_uses_full_utf8_envelope_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            before = repository_bytes(repo)

            exact = exact_candidate_batch(context_cli.MAX_CANDIDATE_BYTES)
            self.assertIn("가", context_cli.canonical_json(exact))
            self.assertLess(
                len(context_cli.canonical_json(exact["candidates"]).encode("utf-8")),
                context_cli.MAX_CANDIDATE_BYTES,
            )
            accepted = public_route(repo, root, exact)
            self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
            accepted_payload = json.loads(accepted.stdout)
            self.assertEqual(16384, accepted_payload["result"]["canonical_bytes"])

            over = copy.deepcopy(exact)
            over["candidates"][-1]["source_refs"][-1] += "x"
            self.assertEqual(16385, len(context_cli.canonical_json(over).encode("utf-8")))
            self.assertLessEqual(
                len(context_cli.canonical_json(over["candidates"]).encode("utf-8")),
                context_cli.MAX_CANDIDATE_BYTES,
            )
            rejected = public_route(repo, root, over)
            self.assertEqual(5, rejected.returncode, rejected.stdout + rejected.stderr)
            self.assertEqual("candidate_batch_too_large", json.loads(rejected.stdout)["error"]["code"])

            eight = {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": [candidate("cand_" + f"{index + 1:032x}") for index in range(8)]}
            self.assertEqual(0, public_route(repo, root, eight).returncode)
            nine = {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": [candidate("cand_" + f"{index + 1:032x}") for index in range(9)]}
            count_rejected = public_route(repo, root, nine)
            self.assertEqual(5, count_rejected.returncode, count_rejected.stdout + count_rejected.stderr)
            self.assertEqual("candidate_batch_too_large", json.loads(count_rejected.stdout)["error"]["code"])
            self.assertEqual(before, repository_bytes(repo))

    def test_public_candidate_batch_dict_envelope_is_exact_and_legacy_list_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            before = repository_bytes(repo)
            valid = {
                "schema": "context-capture-batch/v1",
                "audit_count": 1,
                "candidates": [candidate("cand_550e8400e29b41d4a716446655440000")],
            }
            legacy = public_route(repo, root, valid["candidates"])
            self.assertEqual(0, legacy.returncode, legacy.stdout + legacy.stderr)
            self.assertEqual(
                len(context_cli.canonical_json(valid).encode("utf-8")),
                json.loads(legacy.stdout)["result"]["canonical_bytes"],
            )

            cases = {
                "extra-admin": ({**valid, "admin": True}, "candidate_invalid"),
                "missing-audit-count": ({key: value for key, value in valid.items() if key != "audit_count"}, "candidate_invalid"),
                "schema-type": ({**valid, "schema": 7}, "candidate_invalid"),
                "schema-value": ({**valid, "schema": "context-capture-batch/v2"}, "candidate_invalid"),
                "audit-bool": ({**valid, "audit_count": True}, "candidate_invalid"),
                "audit-string": ({**valid, "audit_count": "1"}, "candidate_invalid"),
                "audit-value": ({**valid, "audit_count": 2}, "audit_repeated"),
                "candidates-type": ({**valid, "candidates": {}}, "candidate_invalid"),
            }
            for name, (malformed, expected_code) in cases.items():
                with self.subTest(name=name):
                    rejected = public_route(repo, root, malformed)
                    self.assertEqual(5, rejected.returncode, rejected.stdout + rejected.stderr)
                    payload = json.loads(rejected.stdout)
                    self.assertFalse(payload["ok"])
                    self.assertEqual(expected_code, payload["error"]["code"])
                    self.assertNotIn("Traceback", rejected.stdout + rejected.stderr)
                    self.assertEqual(before, repository_bytes(repo))

    def test_acceptance_19_unavailable(self) -> None:
        value = candidate("cand_550e8400e29b41d4a716446655440000", requested="decision")
        routed = context_cli.route_candidates([value], context_cli.capabilities_result(), [])
        self.assertEqual("owner_unavailable", routed["routes"][0]["status"])
        self.assertNotEqual("observation", routed["routes"][0].get("target_kind"))

        specialized = candidate("cand_123e4567e89b42d3a456426614174000")
        capabilities = context_cli.capabilities_result()
        capabilities["owners"].append(decision_capability())
        fallback_capability = context_cli.builtin_capability("observation")
        fallback_result = result(specialized, fallback_capability, "decline")
        routed = context_cli.route_candidates([specialized], capabilities, [fallback_result])
        self.assertEqual("owner_unavailable", routed["routes"][0]["status"])
        self.assertEqual("specialized_owner_result_missing", routed["routes"][0]["reason"])

    def test_acceptance_20_invalid_type(self) -> None:
        value = candidate("cand_550e8400e29b41d4a716446655440000", requested="decision")
        capability = decision_capability()
        declined = result(value, capability, "decline")
        routed = context_cli.route_candidates([value], {"schema": "context-owner-capabilities/v1", "owners": [capability]}, [declined])
        self.assertEqual("skipped", routed["routes"][0]["status"])
        self.assertEqual("owner_decline", routed["routes"][0]["reason"])
        self.assertNotIn("authority", routed["routes"][0])

    def test_acceptance_22_owner_call_contract(self) -> None:
        value = candidate("cand_550e8400e29b41d4a716446655440000")
        capability = decision_capability()
        declined = result(value, capability, "decline")
        before = copy.deepcopy(declined)
        routed = context_cli.route_candidates([value], {"schema": "context-owner-capabilities/v1", "owners": [capability]}, [declined])
        self.assertEqual(0, routed["router_owner_process_invocations"])
        self.assertEqual(0, routed["cache_probe_count"])
        self.assertEqual(0, routed["alternate_runtime_count"])
        self.assertEqual(before, declined)

    def test_same_claim_text_is_not_a_mechanical_duplicate_but_duplicate_ids_fail_closed(self) -> None:
        first = candidate("cand_550e8400e29b41d4a716446655440000", specialized=[], fallback="observation")
        second = candidate("cand_123e4567e89b42d3a456426614174000", specialized=[], fallback="observation")
        validated = context_cli.validate_candidate_batch([first, second], context_cli.capabilities_result())
        self.assertEqual([first, second], validated)

        duplicate_id = copy.deepcopy(second)
        duplicate_id["candidate_id"] = first["candidate_id"]
        with self.assertRaises(context_cli.ContextError) as caught:
            context_cli.validate_candidate_batch([first, duplicate_id], context_cli.capabilities_result())
        self.assertEqual("candidate_invalid", caught.exception.code)

        legacy = copy.deepcopy(first)
        legacy["claim_key"] = "same"
        with self.assertRaises(context_cli.ContextError) as caught:
            context_cli.validate_candidate_batch([legacy], context_cli.capabilities_result())
        self.assertEqual("schema_removed_field", caught.exception.code)

        oversized = copy.deepcopy(first)
        oversized["owner_inputs"]["observation"]["observation"] = "가" * 3000
        with self.assertRaises(context_cli.ContextError) as caught:
            context_cli.route_candidates([oversized], context_cli.capabilities_result(), [])
        self.assertEqual("candidate_too_large", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
