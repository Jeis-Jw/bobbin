#!/usr/bin/env python3
from __future__ import annotations

import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


PLUGIN = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file()) / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/context/scripts/context_cli.py"
SPEC = importlib.util.spec_from_file_location("context_cli_owner_descriptor_v2", CLI_PATH)
assert SPEC and SPEC.loader
context_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = context_cli
SPEC.loader.exec_module(context_cli)


def vault_dir() -> tempfile.TemporaryDirectory[str]:
    temp = tempfile.TemporaryDirectory()
    return temp


def context_cli_refresh(repo: Path) -> dict:
    completed = subprocess.run([sys.executable, str(CLI_PATH), "refresh", "--json"], cwd=repo, capture_output=True, text=True)
    return json.loads(completed.stdout)["result"]


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def owner_descriptor(
    *,
    kind: str = "premise",
    owner: str = "context-fixture",
    artifact_schema: str | None = None,
) -> dict:
    artifact_schema = artifact_schema or f"context-{kind}/v1"
    return {
        "schema": "context-owner-descriptor/v2",
        "owner": owner,
        "kind": kind,
        "artifact_schema": artifact_schema,
        "authority": "provisional",
        "structural_profile": {
            "schema": "context-structural-profile/v1",
            "fields": {
                "scope": {"type": "string", "required": True, "min_chars": 1, "max_chars": 160},
                "status": {"type": "enum", "required": True, "values": ["open", "confirmed"]},
                "related": {"type": "relation_map", "required": False, "keys": ["supports"], "max_items": 12},
                "retired_at": {"type": "timestamp", "required": False},
                "retired_reason": {"type": "enum", "required": False, "values": ["refuted", "superseded"]},
                "retirement_note": {"type": "string", "required": False, "min_chars": 1, "max_chars": 500},
                "superseded_by": {"type": "context_id", "required": False},
                "supersedes": {"type": "context_id_list", "required": False, "min_items": 0, "max_items": 12},
            },
            "sections": {
                "ordered": ["Claim", "Evidence", "Impact"],
                "required": ["Claim", "Evidence"],
                "primary": "Claim",
            },
            "index_projection": ["scope", "status"],
            "lifecycle": {
                "allowed_topologies": [
                    "create_current",
                    "replace_same_state",
                    "retire_current",
                    "supersede_current",
                    "delete_one",
                ],
                "reasons": {
                    "refuted": {
                        "topology": "retire_current",
                        "required_fields": ["retired_at", "retired_reason", "retirement_note"],
                        "forbidden_fields": ["superseded_by"],
                        "successor": "forbidden",
                        "references": [],
                    },
                    "superseded": {
                        "topology": "supersede_current",
                        "required_fields": ["retired_at", "retired_reason", "superseded_by"],
                        "forbidden_fields": ["retirement_note"],
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


def area_seed(descriptor: dict) -> str:
    seed = context_cli._area_seed(
        descriptor["kind"],
        descriptor["owner"],
        descriptor["artifact_schema"],
        descriptor["authority"],
        "Project-scoped premises managed by an addon owner.",
        projection_fields=tuple(descriptor["structural_profile"]["index_projection"]),
    )
    return context_cli.render_area_profile(seed, descriptor)


def owner_capability(descriptor: dict) -> dict:
    return {
        "schema": "context-owner-capability/v1",
        "owner": descriptor["owner"],
        "kind": descriptor["kind"],
        "artifact_schema": descriptor["artifact_schema"],
        "authority": descriptor["authority"],
        "descriptor_digest": context_cli.canonical_digest(descriptor),
        "claim_surface": {"type": "agent_skill", "name": f"{descriptor['owner']}:{descriptor['kind']}", "operation": "claim"},
        "claim_assertions": ["claim_present", "evidence_present"],
    }


def claim_result(
    descriptor: dict,
    capability: dict,
    *,
    identifier: str = "ctx_550e8400e29b41d4a716446655440000",
    path: str = "context/premise/fixture.md",
) -> dict:
    candidate = {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_550e8400e29b41d4a716446655440000",
        "claim": "Runtime ownership stays inside the project boundary.",
        "evidence": ["fixture evidence"],
    }
    content = context_cli.render_document(
        {
            "schema": descriptor["artifact_schema"],
            "id": identifier,
            "title": "Fixture premise",
            "summary": "A provisional project premise.",
            "created_at": "2026-08-21T12:00:00+09:00",
            "captured_from": "manual",
            "scope": "project/runtime",
            "status": "open",
        },
        {"Claim": candidate["claim"], "Evidence": "fixture evidence"},
        descriptor,
    )
    input_digest = context_cli.canonical_digest(candidate)
    effect_id = "effect_create_premise"
    return {
        "schema": "context-owner-result/v1",
        "result_type": "claim",
        "transition": "capture",
        "owner": descriptor["owner"],
        "target_kind": descriptor["kind"],
        "candidate_id": candidate["candidate_id"],
        "decision": "claim",
        "reason": "fixture owner semantic decision",
        "capability_digest": context_cli.canonical_digest(capability),
        "semantic_inputs": [{
            "operation": "claim",
            "input_schema": candidate["schema"],
            "input_digest": input_digest,
            "value": candidate,
        }],
        "semantic_attestations": [{
            "schema": "context-semantic-attestation/v1",
            "operation": "claim",
            "input_schema": candidate["schema"],
            "input_digest": input_digest,
            "assertions": [
                {"name": "claim_present", "value": True, "evidence_pointers": ["/claim"]},
                {"name": "evidence_present", "value": True, "evidence_pointers": ["/evidence/0"]},
            ],
        }],
        "artifact_drafts": [{
            "effect_id": effect_id,
            "path": path,
            "content": content,
            "semantic_projection": {
                "kind": descriptor["kind"],
                "primary_claim": candidate["claim"],
                "supporting_context": ["fixture evidence"],
            },
        }],
        "effects": [{
            "effect_id": effect_id,
            "action": "create",
            "area": descriptor["kind"],
            "id": identifier,
            "state": "current",
        }],
        "proposed_plan": {
            "schema": "context-owner-plan/v1",
            "transition": "capture",
            "operations": [{"op": "create", "effect_id": effect_id, "area": descriptor["kind"], "path": path}],
        },
    }


def owner_receipt(
    repo: Path,
    descriptor: dict,
    capability: dict,
    result: dict,
    topology: str,
    *,
    prior_same_area_bundle_digests: list[str] | None = None,
) -> dict:
    semantic_digests = {
        item["operation"]: item["input_digest"]
        for item in result["semantic_inputs"]
    }
    receipt = {
        "schema": "context-owner-validation-receipt/v2",
        "owner": descriptor["owner"],
        "kind": descriptor["kind"],
        "descriptor_digest": context_cli.canonical_digest(descriptor),
        "capability": capability,
        "owner_result_digest": context_cli.canonical_digest(result),
        "base_area_index_sha256": context_cli.sha256_bytes(
            (repo / f"context/{descriptor['kind']}/{descriptor['kind']}.index.md").read_bytes()
        ),
        "prior_same_area_bundle_digests": prior_same_area_bundle_digests or [],
        "transition_topology": topology,
        "semantic_input_digests": semantic_digests,
        "status": "valid",
    }
    receipt["receipt_digest"] = context_cli.canonical_digest(receipt)
    return receipt


def supersede_result(
    repo: Path,
    descriptor: dict,
    capability: dict,
    *,
    reciprocal: bool,
) -> dict:
    predecessor_id = "ctx_550e8400e29b41d4a716446655440000"
    successor_id = "ctx_550e8400e29b41d4a716446655440001"
    source_path = "context/premise/fixture.md"
    source = context_cli.parse_document((repo / source_path).read_text(encoding="utf-8"), descriptor)
    predecessor_frontmatter = dict(source.frontmatter)
    predecessor_frontmatter.update({
        "retired_at": "2026-08-21T13:00:00+09:00",
        "retired_reason": "superseded",
        "superseded_by": successor_id,
    })
    successor_frontmatter = {
        "schema": descriptor["artifact_schema"],
        "id": successor_id,
        "title": "Refined fixture premise",
        "summary": "A refined provisional project premise.",
        "created_at": "2026-08-21T13:00:00+09:00",
        "captured_from": "manual",
        "scope": "project/runtime",
        "status": "confirmed",
    }
    if reciprocal:
        successor_frontmatter["supersedes"] = [predecessor_id]
    predecessor_content = context_cli.render_document(predecessor_frontmatter, source.sections, descriptor)
    successor_content = context_cli.render_document(
        successor_frontmatter,
        {"Claim": "The refined runtime boundary is confirmed.", "Evidence": "replacement evidence"},
        descriptor,
    )
    request = {
        "schema": "context-domain-mutation-input/v1",
        "transition": "profile_supersede",
        "owner": descriptor["owner"],
        "target_kind": descriptor["kind"],
        "requested_changes": {"predecessor": predecessor_id, "successor": successor_id},
        "targets": [{
            "id": predecessor_id,
            "path": source_path,
            "sha256": context_cli.sha256_bytes((repo / source_path).read_bytes()),
        }],
        "successor_owner_result_digest": None,
    }
    request_digest = context_cli.canonical_digest(request)
    retire_effect = "effect_retire_premise"
    create_effect = "effect_create_successor"
    return {
        "schema": "context-owner-result/v1",
        "result_type": "mutation",
        "transition": "profile_supersede",
        "owner": descriptor["owner"],
        "target_kind": descriptor["kind"],
        "capability_digest": context_cli.canonical_digest(capability),
        "semantic_inputs": [{
            "operation": "mutation_request",
            "input_schema": request["schema"],
            "input_digest": request_digest,
            "value": request,
        }],
        "semantic_attestations": [],
        "artifact_drafts": [
            {
                "effect_id": retire_effect,
                "path": "context/premise/retired/fixture.md",
                "content": predecessor_content,
                "semantic_projection": {
                    "kind": descriptor["kind"],
                    "primary_claim": source.sections["Claim"],
                    "supporting_context": [],
                },
            },
            {
                "effect_id": create_effect,
                "path": "context/premise/refined.md",
                "content": successor_content,
                "semantic_projection": {
                    "kind": descriptor["kind"],
                    "primary_claim": "The refined runtime boundary is confirmed.",
                    "supporting_context": ["replacement evidence"],
                },
            },
        ],
        "effects": [
            {"effect_id": retire_effect, "action": "retire", "area": descriptor["kind"], "id": predecessor_id, "state": "history"},
            {"effect_id": create_effect, "action": "create", "area": descriptor["kind"], "id": successor_id, "state": "current"},
        ],
        "proposed_plan": {
            "schema": "context-owner-plan/v1",
            "transition": "profile_supersede",
            "operations": [
                {
                    "op": "move",
                    "effect_id": retire_effect,
                    "area": descriptor["kind"],
                    "id": predecessor_id,
                    "from_path": source_path,
                    "to_path": "context/premise/retired/fixture.md",
                },
                {
                    "op": "create",
                    "effect_id": create_effect,
                    "area": descriptor["kind"],
                    "path": "context/premise/refined.md",
                },
            ],
        },
    }


class OwnerDescriptorV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = tempfile.TemporaryDirectory()
        self.input_root = Path(self.inputs.name)

    def tearDown(self) -> None:
        self.inputs.cleanup()

    def _input(self, name: str, content: str) -> Path:
        path = self.input_root / name
        path.write_text(content, encoding="utf-8")
        return path

    def _json_input(self, name: str, value: object, *, canonical: bool = True) -> Path:
        content = context_cli.canonical_json(value) if canonical else json.dumps(value, ensure_ascii=False, indent=2)
        return self._input(name, content)

    def _cli(self, repo: Path, *arguments: str, expected: int = 0) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            [sys.executable, str(CLI_PATH), *arguments],
            cwd=repo,
            text=True,
            capture_output=True,
        )
        self.assertEqual(expected, completed.returncode, completed.stdout + completed.stderr)
        return completed, json.loads(completed.stdout)

    def _init(self, repo: Path) -> dict:
        return self._cli(repo, "init", "--host", "codex", "--json")[1]["result"]

    def _apply(self, repo: Path, result: dict, name: str = "bundle.json") -> dict:
        bundle_path = self._json_input(name, result["bundle"])
        return self._cli(
            repo,
            "transaction",
            "apply",
            "--plan-bundle",
            f"@{bundle_path}",
            "--approved-digest",
            result["approval_digest"],
            "--json",
        )[1]["result"]

    def _register(self, repo: Path, descriptor: dict, seed: str, prefix: str = "profile") -> dict:
        descriptor_path = self._json_input(f"{prefix}-descriptor.json", descriptor)
        seed_path = self._input(f"{prefix}-index.md", seed)
        result = self._cli(
            repo,
            "area",
            "register",
            "--descriptor",
            f"@{descriptor_path}",
            "--index-seed",
            f"@{seed_path}",
            "--json",
        )[1]["result"]
        if not result.get("noop"):
            self._apply(repo, result, f"{prefix}-register-bundle.json")
        return result

    def _register_profile(self, repo: Path) -> tuple[dict, dict]:
        descriptor = owner_descriptor()
        self._register(repo, descriptor, area_seed(descriptor))
        return descriptor, owner_capability(descriptor)

    def _preview(
        self,
        repo: Path,
        result: dict,
        receipt: dict,
        *,
        expected: int = 0,
        prefix: str = "owner",
        prior_results: list[dict] | None = None,
    ) -> dict:
        result_path = self._json_input(f"{prefix}-result.json", result)
        receipt_path = self._json_input(f"{prefix}-receipt.json", receipt)
        prior_arguments: list[str] = []
        for index, prior in enumerate(prior_results or []):
            prior_path = self._json_input(f"{prefix}-prior-{index}.json", prior["bundle"])
            prior_arguments.extend(["--prior-bundle", f"@{prior_path}"])
        return self._cli(
            repo,
            "transaction",
            "preview",
            "--owner-result",
            f"@{result_path}",
            "--owner-validation",
            f"@{receipt_path}",
            *prior_arguments,
            "--json",
            expected=expected,
        )[1]

    def test_acceptance_49_v1_exact_compatibility(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            self.assertEqual(
                "sha256:6524d53a80f1bd2caf54cf78bfc04d165405522b1ae7f1fbaed180bb12b1681f",
                context_cli.sha256_bytes((repo / context_cli.ROOT_INDEX).read_bytes()),
            )
            descriptor = {
                "schema": "context-owner-descriptor/v1",
                "owner": "context-decision",
                "kind": "decision",
                "artifact_schema": "context-decision/v1",
                "authority": "authoritative",
            }
            seed = context_cli._area_seed(
                "decision",
                "context-decision",
                "context-decision/v1",
                "authoritative",
                "결정·취지·반려대안과 현재 유효성을 관리한다.",
                projection_fields=("scope", "decision_key", "revisit_on"),
            )
            self.assertEqual(
                "sha256:0324c4b8fe3087d091cbe47313d15ca2a3502aa8f19c899b23ef6482bccb2436",
                context_cli.sha256_bytes(context_cli.file_bytes(seed)),
            )
            self._register(repo, descriptor, seed, "v1")
            root_text = (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8")
            self.assertEqual(
                "sha256:0c62f5f242cb4752eb8a469ce271dbfe73fe23f4a13fd927f76481b393baae73",
                context_cli.sha256_bytes(root_text.encode("utf-8")),
            )
            self.assertNotIn("CONTEXT GENERATED:owner-profiles", root_text)
            before = tree_digest(repo)
            repeated = self._register(repo, descriptor, seed, "v1-repeat")
            self.assertTrue(repeated["noop"])
            self.assertEqual(before, tree_digest(repo))

    def test_acceptance_50_v2_mixed_bootstrap_retry(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            v1_descriptor = {
                "schema": "context-owner-descriptor/v1",
                "owner": "context-decision",
                "kind": "decision",
                "artifact_schema": "context-decision/v1",
                "authority": "authoritative",
            }
            v1_seed = context_cli._area_seed(
                "decision", "context-decision", "context-decision/v1", "authoritative", "Decision fixture."
            )
            self._register(repo, v1_descriptor, v1_seed, "mixed-v1")
            descriptor = owner_descriptor()
            descriptor_path = self._json_input("mixed-v2-descriptor.json", descriptor)
            seed_path = self._input("mixed-v2-index.md", area_seed(descriptor))
            first = self._cli(
                repo,
                "bootstrap",
                "--descriptor",
                f"@{descriptor_path}",
                "--index-seed",
                f"@{seed_path}",
                "--host",
                "codex",
                "--json",
            )[1]["result"]
            self.assertEqual("applied", first["phases"][1]["status"])
            root_text = (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8")
            profiles = context_cli.parse_root_profiles(root_text)
            self.assertEqual(["premise"], [item["area"] for item in profiles])
            self.assertEqual(descriptor, context_cli.parse_area_profile((repo / "context/premise/premise.index.md").read_text(encoding="utf-8")))
            schema = self._cli(repo, "schema", "--json")[1]["result"]
            self.assertIn("context-owner-descriptor/v2", schema["features"])
            before = tree_digest(repo)
            repeated = self._cli(
                repo,
                "bootstrap",
                "--descriptor",
                f"@{descriptor_path}",
                "--index-seed",
                f"@{seed_path}",
                "--host",
                "codex",
                "--json",
            )[1]["result"]
            self.assertTrue(repeated["noop"])
            self.assertEqual(before, tree_digest(repo))

    def test_acceptance_51_profile_fail_closed(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            descriptor = owner_descriptor()
            seed_path = self._input("invalid-index.md", area_seed(descriptor))
            invalid_inputs: list[tuple[str, str]] = []
            unknown = dict(descriptor, unknown=True)
            invalid_inputs.append(("unknown", context_cli.canonical_json(unknown)))
            oversized = dict(descriptor, owner="x" * 9000)
            invalid_inputs.append(("oversized", context_cli.canonical_json(oversized)))
            canonical = context_cli.canonical_json(descriptor)
            invalid_inputs.append(("duplicate", canonical.replace(
                '"schema":"context-owner-descriptor/v2"',
                '"schema":"context-owner-descriptor/v2","schema":"context-owner-descriptor/v2"',
                1,
            )))
            invalid_inputs.append(("noncanonical", json.dumps(descriptor, ensure_ascii=False, indent=2)))
            for label, content in invalid_inputs:
                with self.subTest(label=label):
                    descriptor_path = self._input(f"invalid-{label}.json", content)
                    before = tree_digest(repo)
                    _, payload = self._cli(
                        repo,
                        "area",
                        "register",
                        "--descriptor",
                        f"@{descriptor_path}",
                        "--index-seed",
                        f"@{seed_path}",
                        "--json",
                        expected=5,
                    )
                    self.assertEqual("owner_descriptor_invalid", payload["error"]["code"])
                    self.assertEqual(before, tree_digest(repo))

            self._register(repo, descriptor, area_seed(descriptor), "immutable-original")
            changed = copy.deepcopy(descriptor)
            changed["structural_profile"]["fields"]["scope"]["max_chars"] = 159
            changed_descriptor = self._json_input("immutable-changed.json", changed)
            changed_seed = self._input("immutable-changed.md", area_seed(changed))
            before = tree_digest(repo)
            _, payload = self._cli(
                repo,
                "area",
                "register",
                "--descriptor",
                f"@{changed_descriptor}",
                "--index-seed",
                f"@{changed_seed}",
                "--json",
                expected=5,
            )
            self.assertEqual("owner_descriptor_conflict", payload["error"]["code"])
            self.assertEqual(before, tree_digest(repo))

    def test_acceptance_52_receipt_digest_and_provisional_binding(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            descriptor, capability = self._register_profile(repo)
            result = claim_result(descriptor, capability)
            receipt = owner_receipt(repo, descriptor, capability, result, "create_current")
            before = tree_digest(repo)
            preview = self._preview(repo, result, receipt)["result"]
            plan = preview["bundle"]["approval_material"]["plan"]
            self.assertEqual("provisional", plan["owner_descriptor"]["authority"])
            self.assertEqual(context_cli.canonical_digest(descriptor), plan["descriptor_digest"])
            self.assertEqual(capability, plan["owner_validation"]["capability"])
            self.assertEqual(before, tree_digest(repo))

            altered_capability = copy.deepcopy(capability)
            altered_capability["descriptor_digest"] = "sha256:" + "0" * 64
            altered_result = copy.deepcopy(result)
            altered_result["capability_digest"] = context_cli.canonical_digest(altered_capability)
            altered_receipt = owner_receipt(repo, descriptor, altered_capability, altered_result, "create_current")
            payload = self._preview(repo, altered_result, altered_receipt, expected=5, prefix="altered-capability")
            self.assertEqual("owner_validation_invalid", payload["error"]["code"])
            self.assertEqual(before, tree_digest(repo))

            stale_receipt = copy.deepcopy(receipt)
            stale_receipt["descriptor_digest"] = "sha256:" + "f" * 64
            stale_receipt["receipt_digest"] = context_cli.canonical_digest({
                key: value for key, value in stale_receipt.items() if key != "receipt_digest"
            })
            payload = self._preview(repo, result, stale_receipt, expected=5, prefix="stale-receipt")
            self.assertEqual("owner_validation_invalid", payload["error"]["code"])
            self.assertEqual(before, tree_digest(repo))

    def test_regression_v2_expanded_recall_and_id_lookup_use_registered_descriptor(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            descriptor, capability = self._register_profile(repo)
            result = claim_result(descriptor, capability)
            receipt = owner_receipt(repo, descriptor, capability, result, "create_current")
            preview = self._preview(repo, result, receipt, prefix="recall-create")["result"]
            self._apply(repo, preview, "recall-create-apply.json")
            before = tree_digest(repo)

            packed = self._cli(
                repo,
                "recall",
                "--area",
                descriptor["kind"],
                "--pack",
                "--json",
            )[1]["result"]
            self.assertEqual(["ctx_550e8400e29b41d4a716446655440000"], [item["id"] for item in packed["items"]])
            self.assertEqual("Runtime ownership stays inside the project boundary.", packed["items"][0]["sections"]["Claim"])

            read = self._cli(
                repo,
                "recall",
                "--read",
                "ctx_550e8400e29b41d4a716446655440000",
                "--json",
            )[1]["result"]
            self.assertEqual(["ctx_550e8400e29b41d4a716446655440000"], [item["id"] for item in read["items"]])
            self.assertIn("Evidence", read["items"][0]["sections"])

            _, unavailable = self._cli(
                repo,
                "discard",
                "--id",
                "ctx_550e8400e29b41d4a716446655440000",
                "--json",
                expected=5,
            )
            self.assertEqual("owner_unavailable", unavailable["error"]["code"])
            self.assertEqual(before, tree_digest(repo))

    def test_regression_v2_cross_area_prior_filter_and_stale_same_area_rejection(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            descriptor_a, capability_a = self._register_profile(repo)
            descriptor_b = owner_descriptor(kind="hypothesis")
            self._register(repo, descriptor_b, area_seed(descriptor_b), "hypothesis")
            capability_b = owner_capability(descriptor_b)

            result_a = claim_result(descriptor_a, capability_a)
            receipt_a = owner_receipt(repo, descriptor_a, capability_a, result_a, "create_current")
            preview_a = self._preview(repo, result_a, receipt_a, prefix="prior-area-a")["result"]

            result_b = claim_result(
                descriptor_b,
                capability_b,
                identifier="ctx_550e8400e29b41d4a716446655440010",
                path="context/hypothesis/fixture.md",
            )
            receipt_b = owner_receipt(repo, descriptor_b, capability_b, result_b, "create_current")
            preview_b = self._preview(
                repo,
                result_b,
                receipt_b,
                prefix="prior-area-b",
                prior_results=[preview_a],
            )["result"]
            plan_b = preview_b["bundle"]["approval_material"]["plan"]
            self.assertEqual([preview_a["approval_digest"]], plan_b["prior_bundle_digests"])
            self.assertEqual([], plan_b["prior_same_area_bundle_digests"])
            self.assertEqual([], plan_b["owner_validation"]["prior_same_area_bundle_digests"])

            self._apply(repo, preview_a, "prior-area-a-apply.json")
            self._apply(repo, preview_b, "prior-area-b-apply.json")
            self.assertTrue((repo / "context/premise/fixture.md").is_file())
            self.assertTrue((repo / "context/hypothesis/fixture.md").is_file())

            same_area_prior_result = claim_result(
                descriptor_a,
                capability_a,
                identifier="ctx_550e8400e29b41d4a716446655440020",
                path="context/premise/prior.md",
            )
            same_area_prior_receipt = owner_receipt(
                repo,
                descriptor_a,
                capability_a,
                same_area_prior_result,
                "create_current",
            )
            same_area_prior = self._preview(
                repo,
                same_area_prior_result,
                same_area_prior_receipt,
                prefix="same-area-prior",
            )["result"]
            dependent_result = claim_result(
                descriptor_a,
                capability_a,
                identifier="ctx_550e8400e29b41d4a716446655440021",
                path="context/premise/dependent.md",
            )
            dependent_receipt = owner_receipt(
                repo,
                descriptor_a,
                capability_a,
                dependent_result,
                "create_current",
                prior_same_area_bundle_digests=[same_area_prior["approval_digest"]],
            )
            dependent = self._preview(
                repo,
                dependent_result,
                dependent_receipt,
                prefix="same-area-dependent",
                prior_results=[same_area_prior],
            )["result"]
            before = tree_digest(repo)
            dependent_path = self._json_input("same-area-dependent-apply.json", dependent["bundle"])
            _, stale = self._cli(
                repo,
                "transaction",
                "apply",
                "--plan-bundle",
                f"@{dependent_path}",
                "--approved-digest",
                dependent["approval_digest"],
                "--json",
                expected=5,
            )
            self.assertEqual("precondition_changed", stale["error"]["code"])
            self.assertEqual(before, tree_digest(repo))

    def test_regression_v2_profile_registry_tamper_blocks_doctor_and_refresh(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            self._register_profile(repo)
            root_path = repo / context_cli.ROOT_INDEX
            root_text = root_path.read_text(encoding="utf-8")
            profiles = context_cli.parse_root_profiles(root_text)
            profiles[0]["descriptor_digest"] = "sha256:" + "0" * 64
            root_path.write_text(context_cli.render_root_profiles(root_text, profiles), encoding="utf-8")
            tampered = tree_digest(repo)

            doctor = self._cli(repo, "doctor", "--json")[1]["result"]
            self.assertEqual("invalid", doctor["repository_state"])
            self.assertEqual(["owner_profile_mismatch"], [issue["code"] for issue in doctor["issues"]])

            refresh = self._cli(repo, "refresh", "--json")[1]["result"]
            self.assertFalse(refresh["ok"])
            self.assertEqual(["owner_profile_mismatch"], [issue["code"] for issue in refresh["issues"]])

            fixed = self._cli(repo, "refresh", "--fix", "index", "--json")[1]["result"]
            self.assertTrue(fixed["noop"])
            self.assertEqual([], fixed["changed_paths"])
            self.assertEqual(["owner_profile_mismatch"], [issue["code"] for issue in fixed["issues"]])
            self.assertEqual(tampered, tree_digest(repo))

    def test_regression_v2_supersede_profile_requires_reciprocal_recipe(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            valid = owner_descriptor()
            invalid = copy.deepcopy(valid)
            invalid["structural_profile"]["lifecycle"]["reasons"]["superseded"]["references"] = [
                {"location": "predecessor", "field": "superseded_by", "target": "successor", "match": "equals"},
            ]
            descriptor_path = self._json_input("missing-reciprocal-descriptor.json", invalid)
            seed_path = self._input("missing-reciprocal-index.md", area_seed(valid))
            before = tree_digest(repo)
            _, payload = self._cli(
                repo,
                "area",
                "register",
                "--descriptor",
                f"@{descriptor_path}",
                "--index-seed",
                f"@{seed_path}",
                "--json",
                expected=5,
            )
            self.assertEqual("owner_descriptor_invalid", payload["error"]["code"])
            self.assertEqual(before, tree_digest(repo))

    def test_acceptance_53_profiled_target_bytes_validation(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            descriptor, capability = self._register_profile(repo)
            result = claim_result(descriptor, capability)
            before = tree_digest(repo)
            original = result["artifact_drafts"][0]["content"]
            mutations = {
                "undeclared-field": (
                    original.replace(
                        'scope: "project/runtime"\n',
                        'scope: "project/runtime"\nundeclared: "semantic owner approved this"\n',
                        1,
                    ),
                    "schema_invalid",
                ),
                "h2-order": (
                    original.replace("## Claim", "## Temporary", 1)
                    .replace("## Evidence", "## Claim", 1)
                    .replace("## Temporary", "## Evidence", 1),
                    "section_schema_error",
                ),
                "required-evidence": (
                    original.replace("## Evidence\n\nfixture evidence\n", "", 1),
                    "section_schema_error",
                ),
                "primary-claim": (
                    original.replace(
                        "## Claim\n\nRuntime ownership stays inside the project boundary.\n\n",
                        "",
                        1,
                    ),
                    "section_schema_error",
                ),
                "projection-scope": (
                    original.replace('scope: "project/runtime"', f'scope: "{"x" * 161}"', 1),
                    "schema_invalid",
                ),
            }
            for label, (content, expected_code) in mutations.items():
                with self.subTest(label=label):
                    self.assertNotEqual(original, content)
                    invalid = copy.deepcopy(result)
                    invalid["artifact_drafts"][0]["content"] = content
                    receipt = owner_receipt(repo, descriptor, capability, invalid, "create_current")
                    payload = self._preview(repo, invalid, receipt, expected=2, prefix=f"invalid-target-{label}")
                    self.assertEqual(expected_code, payload["error"]["code"])
                    self.assertEqual(before, tree_digest(repo))

    def test_acceptance_54_generic_lifecycle_relations(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            descriptor, capability = self._register_profile(repo)
            initial = claim_result(descriptor, capability)
            initial_receipt = owner_receipt(repo, descriptor, capability, initial, "create_current")
            initial_preview = self._preview(repo, initial, initial_receipt, prefix="initial")["result"]
            self._apply(repo, initial_preview, "initial-apply.json")

            malformed = supersede_result(repo, descriptor, capability, reciprocal=False)
            malformed_receipt = owner_receipt(repo, descriptor, capability, malformed, "supersede_current")
            before = tree_digest(repo)
            payload = self._preview(repo, malformed, malformed_receipt, expected=5, prefix="bad-relation")
            self.assertEqual("reference_invalid", payload["error"]["code"])
            self.assertEqual(before, tree_digest(repo))

            valid = supersede_result(repo, descriptor, capability, reciprocal=True)
            valid_receipt = owner_receipt(repo, descriptor, capability, valid, "supersede_current")
            valid_preview = self._preview(repo, valid, valid_receipt, prefix="valid-relation")["result"]
            self.assertEqual("supersede_current", valid_preview["bundle"]["approval_material"]["plan"]["transition_topology"])
            self._apply(repo, valid_preview, "valid-relation-apply.json")
            index = context_cli.parse_area_index((repo / "context/premise/premise.index.md").read_text(encoding="utf-8"))
            self.assertEqual(["ctx_550e8400e29b41d4a716446655440001"], [row["id"] for row in index.current])
            self.assertEqual(["ctx_550e8400e29b41d4a716446655440000"], [row["id"] for row in index.history])
            self.assertEqual("ctx_550e8400e29b41d4a716446655440001", index.history[0]["superseded_by"])
            doctor = self._cli(repo, "doctor", "--json")[1]["result"]
            self.assertEqual("ready", doctor["repository_state"])
            self.assertEqual([], doctor["issues"])

    def test_typed_relations_validate_target_kind_at_preview_apply_and_doctor(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)

            observation = context_cli.direct_candidate(
                "observation",
                title="Runtime observation",
                summary="Typed relation target fixture.",
                captured_from="manual",
                owner_inputs={"observation": "The runtime boundary is observable.", "evidence": ["fixture evidence"]},
            )
            observation_attestation = {
                "schema": "context-semantic-attestation/v1",
                "operation": "claim",
                "input_schema": observation["schema"],
                "input_digest": context_cli.canonical_digest(observation),
                "assertions": [
                    {"name": "reusable_observation", "value": True, "evidence_pointers": ["/owner_inputs/observation/observation"]},
                    {"name": "evidence_present", "value": True, "evidence_pointers": ["/owner_inputs/observation/evidence/0"]},
                ],
            }
            observation_result = context_cli.draft_owner_result(
                observation,
                observation_attestation,
                identifier="ctx_550e8400e29b41d4a716446655440010",
                now="2026-08-21T11:00:00+09:00",
            )
            observation_preview = context_cli.finalize_owner_result(repo, observation_result)
            context_cli.apply_bundle(repo, observation_preview["bundle"], observation_preview["approval_digest"])

            descriptor = owner_descriptor()
            descriptor["structural_profile"]["fields"].pop("related")
            descriptor["structural_profile"]["fields"]["relations"] = {
                "type": "relation_map",
                "required": False,
                "keys": ["supports", "supports:observation", "supports:assumption"],
                "max_items": 12,
            }
            self._register(repo, descriptor, area_seed(descriptor), "typed")
            capability = owner_capability(descriptor)

            def related_result(key: str, target: str) -> dict:
                result = claim_result(descriptor, capability)
                document = context_cli.parse_document(result["artifact_drafts"][0]["content"], descriptor)
                frontmatter = dict(document.frontmatter)
                frontmatter["relations"] = {key: [target]}
                result["artifact_drafts"][0]["content"] = context_cli.render_document(frontmatter, document.sections, descriptor)
                return result

            before = tree_digest(repo)
            for key, target, expected_code in (
                ("supports:observation", "ctx_550e8400e29b41d4a716446655440099", "typed_relation_target_missing"),
                ("supports:assumption", "ctx_550e8400e29b41d4a716446655440010", "typed_relation_kind_mismatch"),
            ):
                with self.subTest(key=key):
                    invalid = related_result(key, target)
                    receipt = owner_receipt(repo, descriptor, capability, invalid, "create_current")
                    with self.assertRaises(context_cli.ContextError) as caught:
                        context_cli.finalize_owner_result(repo, invalid, receipt)
                    self.assertEqual(expected_code, caught.exception.code)
                    self.assertEqual(before, tree_digest(repo))

            legacy = related_result("supports", "ctx_550e8400e29b41d4a716446655440010")
            legacy_receipt = owner_receipt(repo, descriptor, capability, legacy, "create_current")
            legacy_preview = context_cli.finalize_owner_result(repo, legacy, legacy_receipt)
            self.assertEqual(before, tree_digest(repo), "preview must remain byte-noop")

            valid = related_result("supports:observation", "ctx_550e8400e29b41d4a716446655440010")
            valid_receipt = owner_receipt(repo, descriptor, capability, valid, "create_current")
            valid_preview = context_cli.finalize_owner_result(repo, valid, valid_receipt)
            observation_path = repo / observation_result["artifact_drafts"][0]["path"]
            observation_path.unlink()
            tampered = tree_digest(repo)
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.apply_bundle(repo, valid_preview["bundle"], valid_preview["approval_digest"])
            self.assertEqual("typed_relation_target_missing", caught.exception.code)
            self.assertEqual(tampered, tree_digest(repo))
            self.assertFalse((repo / "context/premise/fixture.md").exists())

            observation_path.write_text(observation_result["artifact_drafts"][0]["content"], encoding="utf-8")
            context_cli.apply_bundle(repo, valid_preview["bundle"], valid_preview["approval_digest"])
            artifact_path = repo / "context/premise/fixture.md"
            text = artifact_path.read_text(encoding="utf-8")
            artifact_path.write_text(text.replace("supports:observation", "supports:assumption"), encoding="utf-8")
            refresh = context_cli.refresh_repository(repo)
            self.assertIn("typed_relation_kind_mismatch", [issue["code"] for issue in refresh["issues"]])
            doctor = context_cli.doctor_repository(repo)
            self.assertIn("typed_relation_kind_mismatch", [issue["code"] for issue in doctor["issues"]])

    def test_acceptance_55_apply_revalidates_after_lock_tamper(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            self._init(repo)
            descriptor, capability = self._register_profile(repo)
            result = claim_result(descriptor, capability)
            receipt = owner_receipt(repo, descriptor, capability, result, "create_current")
            preview = self._preview(repo, result, receipt, prefix="locked")["result"]
            bundle_path = self._json_input("locked-bundle.json", preview["bundle"])
            index_path = repo / "context/premise/premise.index.md"
            fixture_path = repo / "context/premise/fixture.md"

            lock_root = Path(tempfile.gettempdir()) / "context-core-locks"
            lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock_path = lock_root / hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()
            # Artifact-level tamper under the lock still fails closed; an index-only tamper
            # (the projection) is re-derived by core instead of blocking the approved write.
            for tamper in ("artifact", "index"):
                with self.subTest(tamper=tamper):
                    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                    completed: subprocess.CompletedProcess[str]
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX)
                        process = subprocess.Popen(
                            [
                                sys.executable,
                                str(CLI_PATH),
                                "transaction",
                                "apply",
                                "--plan-bundle",
                                f"@{bundle_path}",
                                "--approved-digest",
                                preview["approval_digest"],
                                "--json",
                            ],
                            cwd=repo,
                            text=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )
                        time.sleep(0.25)
                        self.assertIsNone(process.poll(), "apply did not wait for the repository lock")
                        if tamper == "artifact":
                            fixture_path.write_text("concurrent artifact\n", encoding="utf-8")
                        else:
                            index_path.write_text(index_path.read_text(encoding="utf-8") + "\n<!-- concurrent tamper -->\n", encoding="utf-8")
                        tampered = tree_digest(repo)
                        fcntl.flock(fd, fcntl.LOCK_UN)
                        stdout, stderr = process.communicate(timeout=5)
                        completed = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
                    finally:
                        try:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        finally:
                            os.close(fd)
                    if tamper == "artifact":
                        self.assertEqual(5, completed.returncode, completed.stdout + completed.stderr)
                        self.assertEqual("precondition_changed", json.loads(completed.stdout)["error"]["code"])
                        self.assertEqual(tampered, tree_digest(repo))
                        fixture_path.unlink()
                    else:
                        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                        applied = json.loads(completed.stdout)["result"]
                        self.assertTrue(applied["applied"])
                        self.assertIn("index_regenerated:context/premise/premise.index.md", applied["warnings"])
                        self.assertTrue(fixture_path.is_file())
                        self.assertIn("<!-- concurrent tamper -->", index_path.read_text(encoding="utf-8"))
                        self.assertEqual([], context_cli_refresh(repo)["issues"])

    def test_acceptance_56_partial_retry_convergence(self) -> None:
        descriptor = owner_descriptor()
        seed_text = area_seed(descriptor)
        for state in ("none", "seed-only", "root-profile-only"):
            with self.subTest(state=state), vault_dir() as temp:
                repo = Path(temp)
                self._init(repo)
                descriptor_path = self._json_input(f"partial-{state}.json", descriptor)
                seed_path = self._input(f"partial-{state}.md", seed_text)
                initial = self._cli(
                    repo,
                    "area",
                    "register",
                    "--descriptor",
                    f"@{descriptor_path}",
                    "--index-seed",
                    f"@{seed_path}",
                    "--json",
                )[1]["result"]
                materials = {item["material_id"]: item for item in initial["bundle"]["materials"]}
                if state == "seed-only":
                    target = repo / materials["seed_area_index"]["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(materials["seed_area_index"]["content"], encoding="utf-8")
                elif state == "root-profile-only":
                    (repo / context_cli.ROOT_INDEX).write_text(materials["material_root_index"]["content"], encoding="utf-8")

                retry = initial if state == "none" else self._cli(
                    repo,
                    "area",
                    "register",
                    "--descriptor",
                    f"@{descriptor_path}",
                    "--index-seed",
                    f"@{seed_path}",
                    "--json",
                )[1]["result"]
                if state == "root-profile-only":
                    self.assertTrue(retry.get("resume_prefix"))
                applied = self._apply(repo, retry, f"partial-{state}-apply.json")
                self.assertIn("context/premise/premise.index.md", applied["index_paths"])
                completed = self._cli(
                    repo,
                    "area",
                    "register",
                    "--descriptor",
                    f"@{descriptor_path}",
                    "--index-seed",
                    f"@{seed_path}",
                    "--json",
                )[1]["result"]
                self.assertTrue(completed["noop"])
                self.assertEqual(descriptor, context_cli.parse_area_profile((repo / "context/premise/premise.index.md").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
