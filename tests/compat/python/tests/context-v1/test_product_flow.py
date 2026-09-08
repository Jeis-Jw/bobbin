#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flow = load("cross_plugin_product_helpers", ROOT / "tests/context-v1/test_cross_plugin_flow.py")
context_cli = flow.context_cli
decision_cli = flow.decision_cli


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


class ProductFlowTests(unittest.TestCase):
    def test_standalone_and_integrated_approval_flows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)

            init = context_cli.build_init_bundle(repo)
            before = tree_digest(repo)
            self.assertFalse(init["applied"])
            self.assertEqual(before, tree_digest(repo))
            context_cli.apply_bundle(repo, init["bundle"], init["approval_digest"])

            observation = flow.choice("cand_123e4567e89b42d3a456426614174000")
            observation.update(
                requested_kind="observation",
                specialized_kinds=["observation"],
                fallback_kind=None,
                title="Safari cookie 관찰",
                claim="Safari에서 third-party cookie가 차단된다.",
                summary="Safari cookie 제한을 재현했다.",
            )
            observation["owner_inputs"] = {
                "observation": {"observation": observation["claim"], "evidence": ["재현 fixture"]}
            }
            obs_result = context_cli.draft_owner_result(observation, flow.obs_attestation(observation))
            obs_preview = context_cli.finalize_owner_result(repo, obs_result)
            before = tree_digest(repo)
            self.assertFalse(obs_preview["applied"])
            self.assertEqual(before, tree_digest(repo))
            context_cli.apply_bundle(repo, obs_preview["bundle"], obs_preview["approval_digest"])

            addon = decision_cli.build_init_plan()
            area = context_cli.build_area_register_bundle(repo, addon["owner_descriptor"], addon["index_seed"])
            context_cli.apply_bundle(repo, area["bundle"], area["approval_digest"])
            choice = flow.choice()
            owner_result = decision_cli.build_claim_result(
                choice,
                flow.decision_attestation(choice),
                repo=repo,
                created_at="2026-08-14T11:00:00+09:00",
            )
            validation = decision_cli.validate_batch(repo, owner_result)
            proposal = context_cli.finalize_owner_result(repo, owner_result, validation)
            before = tree_digest(repo)
            preview_text = proposal["approval_preview"]["artifacts"][0]["content"]
            for section in ("## Decision", "## Rationale", "## Rejected alternatives"):
                self.assertIn(section, preview_text)
            self.assertEqual(before, tree_digest(repo))
            context_cli.apply_bundle(repo, proposal["bundle"], proposal["approval_digest"])
            decision_index = (repo / "context/decision/decision.index.md").read_text(encoding="utf-8")
            _, current_rows, _ = decision_cli.parse_decision_index(decision_index)
            self.assertEqual({"인증 주체", "세션 owner"}, set(current_rows[0]["terms"]))

            decision_id = owner_result["effects"][0]["id"]
            brief = decision_cli.brief_decisions(repo, identifiers=[decision_id])
            sections = brief["items"][0]["sections"]
            self.assertIn("인증 세션은 BFF가 소유", sections["Decision"])
            self.assertIn("cookie 차이", sections["Rationale"])
            self.assertIn("SPA token", sections["Rejected alternatives"])

            successor = flow.choice("cand_987e6543e21b42d3a456426614174002")
            successor["claim"] = "인증 세션은 auth service가 소유한다."
            successor["owner_inputs"]["decision"]["decision"] = successor["claim"]
            successor["owner_inputs"]["decision"]["rationale"] = "BFF와 worker가 같은 session lifecycle을 공유한다."
            supersede = decision_cli.build_supersede_result(
                repo,
                decision_id,
                successor,
                flow.decision_attestation(successor),
                retired_at="2026-08-14T12:00:00+09:00",
            )
            supersede_receipt = decision_cli.validate_batch(repo, supersede)
            supersede_preview = context_cli.finalize_owner_result(repo, supersede, supersede_receipt)
            context_cli.apply_bundle(repo, supersede_preview["bundle"], supersede_preview["approval_digest"])
            history = decision_cli.brief_decisions(repo, identifiers=[decision_id], include_history=True)
            self.assertTrue(history["items"][0]["do_not_follow"])
            self.assertEqual("superseded", history["items"][0]["lifecycle_reason"])

    def test_acceptance_40_obsidian_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            flow.initialize(repo)
            candidate = flow.choice()
            result = decision_cli.build_claim_result(candidate, flow.decision_attestation(candidate), repo=repo)
            receipt = decision_cli.validate_batch(repo, result)
            bundle = context_cli.finalize_owner_result(repo, result, receipt)
            context_cli.apply_bundle(repo, bundle["bundle"], bundle["approval_digest"])

            root_index = (repo / "context/context.index.md").read_text(encoding="utf-8")
            area_index = (repo / "context/decision/decision.index.md").read_text(encoding="utf-8")
            artifact = result["artifact_drafts"][0]["path"]
            self.assertIn("[[context/decision/decision.index]]", root_index)
            self.assertIn(f"[[{artifact[:-3]}]]", area_index)
            self.assertTrue((repo / artifact).is_file())
            self.assertNotIn("obsidian", " ".join(sys.modules[name].__name__ for name in sys.modules).casefold())


if __name__ == "__main__":
    unittest.main()
