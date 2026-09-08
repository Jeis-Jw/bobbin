#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import importlib.util
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


core_cli = load("context_core_semantic_limits", ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py")
decision_cli = load("context_decision_semantic_limits", ROOT / "plugins/bobbin/skills/decision/scripts/decision_cli.py")
assumption_cli = load("context_assumption_semantic_limits", ROOT / "plugins/bobbin/skills/assumption/scripts/assumption_cli.py")
term_cli = load("context_term_semantic_limits", ROOT / "plugins/bobbin/skills/term/scripts/term_cli.py")


def decision_candidate(decision: str) -> dict:
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_550e8400e29b41d4a716446655440000",
        "title": "결정 길이 경계",
        "claim": decision,
        "summary": "결정 section의 codepoint 경계를 검증한다.",
        "captured_from": "conversation",
        "requested_kind": "decision",
        "specialized_kinds": ["decision"],
        "fallback_kind": None,
        "scope_hint": "project/limits",
        "evidence": ["결정권자가 현재 따를 선택으로 확정했다."],
        "owner_inputs": {
            "decision": {
                "decision": decision,
                "rationale": "경계값 회귀를 고정한다.",
                "rejected_alternatives": ["검토하지 않음: 경계 테스트"],
                "decision_key": "decision-length",
            }
        },
    }


def term_candidate(definition: str) -> dict:
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_650e8400e29b41d4a716446655440000",
        "title": "용어 정의 길이 경계",
        "claim": definition,
        "summary": "TERM definition codepoint 경계를 검증한다.",
        "captured_from": "conversation",
        "requested_kind": "term",
        "specialized_kinds": ["term"],
        "fallback_kind": None,
        "scope_hint": "project/limits",
        "owner_inputs": {
            "term": {
                "term": "Boundary Term",
                "definition": definition,
                "project_signal": "project-specific",
            }
        },
    }


def transport_candidate(kind: str, claim: str, owner_input: dict) -> dict:
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_750e8400e29b41d4a716446655440000",
        "title": "공통 transport 경계",
        "claim": claim,
        "summary": "공통 primary claim과 owner input 경계를 검증한다.",
        "captured_from": "conversation",
        "requested_kind": kind,
        "specialized_kinds": [kind],
        "fallback_kind": None,
        "owner_inputs": {kind: owner_input},
    }


def opaque_owner_input(module, size: int) -> dict:
    value = {"payload": ""}
    remaining = size - len(module.canonical_json(value).encode("utf-8"))
    if remaining < 0:
        raise AssertionError(f"unrepresentable owner input boundary: {size}")
    value["payload"] = "x" * remaining
    assert len(module.canonical_json(value).encode("utf-8")) == size
    return value


def sized_decision_candidate(owner_input_bytes: int) -> dict:
    value = decision_candidate("d")
    owner = value["owner_inputs"]["decision"]
    owner.update(
        rationale="r",
        rejected_alternatives=["a"] * 8,
        constraints=["c"] * 8,
        tradeoffs=["t"] * 8,
        revisit_when=["v"] * 8,
    )
    remaining = owner_input_bytes - len(decision_cli.canonical_json(owner).encode("utf-8"))
    slots = [("rationale", None, 1200), *(('rejected_alternatives', index, 500) for index in range(8))]
    slots.extend((field, index, 240) for field in ("constraints", "tradeoffs", "revisit_when") for index in range(8))
    for field, index, maximum in slots:
        current = owner[field] if index is None else owner[field][index]
        growth = min(remaining, maximum - len(current))
        updated = current + "x" * growth
        if index is None:
            owner[field] = updated
        else:
            owner[field][index] = updated
        remaining -= growth
    if remaining:
        raise AssertionError(f"unrepresentable decision owner input boundary: {owner_input_bytes}")
    assert len(decision_cli.canonical_json(owner).encode("utf-8")) == owner_input_bytes
    return value


class SemanticInputLimitTests(unittest.TestCase):
    def test_observation_evidence_uses_six_default_read_slots(self) -> None:
        capability = core_cli.builtin_capability("observation")
        self.assertEqual(6, capability["draft_fields"]["required"]["evidence"]["max_items"])
        accepted = core_cli.direct_candidate(
            "observation",
            title="evidence slots",
            summary="six bounded evidence slots are accepted",
            captured_from="manual",
            owner_inputs={"observation": "bounded evidence claim", "evidence": [f"evidence {index}" for index in range(6)]},
        )
        self.assertEqual(6, len(accepted["owner_inputs"]["observation"]["evidence"]))
        with self.assertRaises(core_cli.ContextError) as rejected:
            core_cli.direct_candidate(
                "observation",
                title="evidence overflow",
                summary="seven evidence slots are rejected",
                captured_from="manual",
                owner_inputs={"observation": "bounded evidence claim", "evidence": [f"evidence {index}" for index in range(7)]},
            )
        self.assertEqual("schema_invalid", rejected.exception.code)

    def test_release_limit_constants_are_in_parity(self) -> None:
        for module in (core_cli, decision_cli, assumption_cli, term_cli):
            self.assertEqual(2000, module.MAX_PRIMARY_CLAIM_CODEPOINTS)
            self.assertEqual(8 * 1024, module.MAX_OWNER_INPUT_BYTES)
            self.assertEqual(16 * 1024, module.MAX_CANDIDATE_BYTES)
        self.assertEqual(1200, decision_cli.MAX_DECISION_CODEPOINTS)
        self.assertEqual(2000, term_cli.term_capability()["draft_fields"]["required"]["definition"]["max_chars"])

    def test_common_claim_and_term_definition_accept_2000_reject_2001(self) -> None:
        accepted = term_candidate("d" * 2000)
        term_cli.validate_term_candidate(accepted)

        rejected = term_candidate("d" * 2001)
        with self.assertRaises(term_cli.TermError) as caught:
            term_cli.validate_term_candidate(rejected)
        self.assertEqual("schema_invalid", caught.exception.code)
        self.assertEqual(
            {"field": "claim", "actual_codepoints": 2001, "maximum_codepoints": 2000, "over_by_codepoints": 1},
            caught.exception.details,
        )

        mismatch = term_candidate("actual definition")
        mismatch["claim"] = "different transport claim"
        with self.assertRaises(term_cli.TermError) as caught:
            term_cli.validate_term_candidate(mismatch)
        self.assertEqual("candidate_invalid", caught.exception.code)

    def test_decision_accepts_1200_rejects_1201_and_dogfood_2182_bytes(self) -> None:
        decision_cli.validate_candidate(decision_candidate("d" * 1200))
        with self.assertRaises(decision_cli.DecisionError) as caught:
            decision_cli.validate_candidate(decision_candidate("d" * 1201))
        self.assertEqual("candidate_invalid", caught.exception.code)
        self.assertEqual(
            {"field": "decision", "actual_codepoints": 1201, "maximum_codepoints": 1200, "over_by_codepoints": 1},
            caught.exception.details,
        )

        dogfood = decision_candidate("결" * 427)
        values = dogfood["owner_inputs"]["decision"]
        values["rationale"] = ""
        remaining = 2182 - len(decision_cli.canonical_json(values).encode("utf-8"))
        values["rationale"] = "r" * remaining
        self.assertEqual(427, len(values["decision"]))
        self.assertEqual(2182, len(decision_cli.canonical_json(values).encode("utf-8")))
        decision_cli.validate_candidate(dogfood)

        mismatch = decision_candidate("actual decision")
        mismatch["claim"] = "ACTUAL DECISION"
        with self.assertRaises(decision_cli.DecisionError) as caught:
            decision_cli.validate_candidate(mismatch)
        self.assertEqual("candidate_invalid", caught.exception.code)

    def test_owner_input_accepts_8192_rejects_8193_with_diagnostics(self) -> None:
        for module, kind in ((assumption_cli, "decision"), (term_cli, "assumption")):
            with self.subTest(module=module.__name__):
                accepted = transport_candidate(kind, "common claim", opaque_owner_input(module, 8192))
                module.validate_transport_candidate(accepted)
                rejected = transport_candidate(kind, "common claim", opaque_owner_input(module, 8193))
                with self.assertRaises((assumption_cli.AssumptionError, term_cli.TermError)) as caught:
                    module.validate_transport_candidate(rejected)
                self.assertEqual("owner_input_too_large", caught.exception.code)
                self.assertEqual(
                    {"kind": kind, "actual_bytes": 8193, "maximum_bytes": 8192, "over_by_bytes": 1},
                    caught.exception.details,
                )

        decision_cli.validate_candidate(sized_decision_candidate(8192))
        with self.assertRaises(decision_cli.DecisionError) as caught:
            decision_cli.validate_candidate(sized_decision_candidate(8193))
        self.assertEqual("owner_input_too_large", caught.exception.code)
        self.assertEqual(
            {"kind": "decision", "actual_bytes": 8193, "maximum_bytes": 8192, "over_by_bytes": 1},
            caught.exception.details,
        )

    def test_candidate_batch_accepts_16384_rejects_16385_with_diagnostics(self) -> None:
        capability = copy.deepcopy(core_cli.builtin_capability("observation"))
        capability["owner"] = "addon-boundary"
        capabilities = {"schema": "context-owner-capabilities/v1", "owners": [capability]}
        candidate = transport_candidate("observation", "common claim", {"payload": "ok"})
        candidate["source_refs"] = [""]
        batch = {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": [candidate]}
        remaining = 16 * 1024 - len(core_cli.canonical_json(batch).encode("utf-8"))
        candidate["source_refs"][0] = "x" * remaining
        self.assertEqual(16 * 1024, len(core_cli.canonical_json(batch).encode("utf-8")))
        core_cli.validate_candidate_batch(batch, capabilities)

        candidate["source_refs"][0] += "x"
        with self.assertRaises(core_cli.ContextError) as caught:
            core_cli.validate_candidate_batch(batch, capabilities)
        self.assertEqual("candidate_batch_too_large", caught.exception.code)
        self.assertEqual(
            {"actual_bytes": 16385, "maximum_bytes": 16384, "over_by_bytes": 1},
            caught.exception.details,
        )

    def test_body_argument_file_literal_and_safety_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            body = root / "body.txt"
            body.write_text("본문\n", encoding="utf-8")
            missing = root / "missing.txt"
            link = root / "body-link.txt"
            link.symlink_to(body)
            oversized = root / "oversized.txt"
            oversized.write_bytes(b"x" * 8193)
            for label, loader, error_type in (
                ("core", core_cli._load_body_argument, core_cli.ContextError),
                ("decision", decision_cli.load_body_argument, decision_cli.DecisionError),
            ):
                with self.subTest(module=label):
                    self.assertEqual("notes/decision.md", loader("notes/decision.md"))
                    self.assertEqual("@literal", loader("@@literal"))
                    self.assertEqual("본문\n", loader(f"@{body}"))
                    with self.assertRaises(error_type) as caught:
                        loader(f"@{missing}")
                    self.assertEqual("input_unavailable", caught.exception.code)
                    with self.assertRaises(error_type) as caught:
                        loader(f"@{link}")
                    self.assertEqual("input_unavailable", caught.exception.code)
                    self.assertEqual("symlink", caught.exception.details["reason"])
                    with self.assertRaises(error_type) as caught:
                        loader(f"@{oversized}")
                    self.assertEqual("input_too_large", caught.exception.code)
                    self.assertEqual(
                        {"path": str(oversized), "actual_bytes": 8193, "maximum_bytes": 8192, "over_by_bytes": 1},
                        caught.exception.details,
                    )

    def test_direct_candidate_uses_file_and_at_literal_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            body = Path(temp) / "decision.txt"
            body.write_text("file decision\n", encoding="utf-8")
            args = argparse.Namespace(
                sec_decision=f"@{body}",
                sec_rationale="@@literal rationale",
                sec_alternatives=["notes/rejected.md"],
                sec_constraints=[],
                sec_tradeoffs=[],
                sec_revisit=[],
                decision_key="body-input",
                revisit_on=None,
                candidate_id="cand_850e8400e29b41d4a716446655440000",
                title="Body input semantics",
                summary="Explicit at-file and literal behavior.",
                captured_from="manual",
                scope="project/input",
                source_ref=[],
                tag=[],
                search_term=[],
                commitment_evidence=["caller confirmed"],
                informed_by=[],
            )
            candidate = decision_cli.build_direct_candidate(args)
            self.assertEqual("file decision", candidate["claim"])
            self.assertEqual("@literal rationale", candidate["owner_inputs"]["decision"]["rationale"])
            self.assertEqual(["notes/rejected.md"], candidate["owner_inputs"]["decision"]["rejected_alternatives"])


if __name__ == "__main__":
    unittest.main()
