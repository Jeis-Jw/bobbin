# Benchmarks and evidence

These measurements cover bytes and tool I/O bounds; they are not end-to-end model token measurements.

## Bobbin 1.0.0 regression check

On 2026-09-05, compare Bobbin commit `64d0e3f822b267b25d5373abc1487ee87c51f194`
(before the repository-coordinate rename) with release-set 0.15.0 commit
`5bac7b22beb751d2964f93567b4f84b4408063fb` on the same machine, using Python
3.13.2 and warm filesystem caches. Each read workload has three untimed warmups
and 31 measured CLI invocations per version, alternating version order. CLI
startup is included. Reads share the same 4,100 Current + 1,000 History records.
The candidate uses an explicit-mode project configuration with a shared vault.

| CLI workload | Before median | Bobbin median | Change |
| --- | ---: | ---: | ---: |
| Metadata hit | 166.840 ms | 167.575 ms | +0.4% |
| Metadata miss | 192.966 ms | 195.001 ms | +1.1% |
| Cross-area recall | 240.197 ms | 240.865 ms | +0.3% |
| Selected body pack | 171.537 ms | 172.548 ms | +0.6% |
| Decision check | 299.421 ms | 300.658 ms | +0.4% |
| Decision record, explicit | 493.876 ms | 501.878 ms | +1.6% |

Auto and adaptive record medians were 500.655 ms and 500.112 ms respectively.
Writes rotate four lanes in separate, initially empty vaults, each growing
identically from three warmup records through 31 measured additions. Every write
must apply, remove its receipt, retain the correct authorization source, and pass
the final index check. Adaptive timing includes a supplied assessment; it does
not include the LLM's work to decide whether to ask.

All compared recall results and selected decision bodies were equal. Instrumented
artifact/index I/O counts and result byte sizes were also equal. Metadata hit and
miss opened zero artifact bodies; the selected pack opened exactly one. These
metrics do not include the new project-config reads; their cost is included in
the CLI timings. Index reads and scoring still scale with corpus size.

This run found a small overhead, not exact performance parity: +0.7–2.0 ms for
reads and +8.0 ms for explicit recording. It does not establish host/LLM latency,
token use, answer quality, cold-cache behavior, or large-vault write performance.
Raw samples, p95 values, I/O counters and runtime source hashes are in
[the measured evidence](tests/context-v1/evidence/bobbin-1.0.0-regression-python313.json).

To reproduce, extract the baseline into a separate temporary directory without
switching or resetting the candidate checkout, then run:

```bash
python3.13 scripts/benchmark_regression.py --baseline /path/to/extracted-0.15.0 --repeats 31
```

The script writes only temporary fixtures and prints JSON. Timings are observations,
not a universal performance threshold. The assertions fail on changed read results,
changed instrumented I/O, failed recording or inconsistent final indexes.

## Reproducible model-free checks

Run the committed token-I/O fixture from the repository root:

```bash
python -m pytest -q -s tests/context-v1/test_token_io_evidence.py
```

On the 0.15.0 development worktree with Python 3.13, the command emitted the following model-free observations. The fixture itself defines the corpus and assertions, so future results should be attributed to the exact commit and interpreter that produced them.

| Contract | Observed result | Committed source |
| --- | --- | --- |
| Healthy metadata miss | 2,000 Current OBS rows; 0 returned artifact bodies and 0 artifact opens | `test_identifier_like_query_is_precise_and_healthy_miss_opens_no_bodies` |
| Default stage 1 | 3,813-byte serialized result, below the 4 KiB contract | `test_stage1_pack_and_section_use_independent_byte_budgets` |
| Selected body pack | 7,803 bytes; 13 returned and 13 opened from a 5,100-record Current+History corpus | same test |
| Narrow selected body pack | 686 bytes; 1 returned and 1 opened under a caller-provided 1 KiB budget | same test |
| Candidate batch | 8 candidates accepted at 3,444 canonical bytes; 9 candidates rejected with `candidate_batch_too_large` | `test_candidate_0_1_8_9_and_exact_owner_input_boundaries` |
| Owner input | 8,192 bytes accepted and 8,193 bytes rejected | same test |
| Approval preview | 27,268-byte envelope containing 27,076 semantic-content bytes, below 32 KiB | `test_grouped_preview_accepts_complete_32k_or_less_without_semantic_truncation` |
| Addon routing | 0, 1, and 8 registered addons each retain one audit and zero router-owner subprocess invocations for an empty batch | `test_addon_count_does_not_multiply_audit_or_router_process_work` |

This command checks deterministic Python behavior, serialized byte ceilings, and instrumented file I/O. It does not measure host prompt assembly, model input tokens, latency, answer quality, or cost.

Run the committed decision-ranking regression separately:

```bash
python -m pytest -q tests/context-v1/test_recall_at_scale.py
```

That fixture checks eight frozen queries at corpus sizes 200 and 1,000. Every target must remain within eight returned bodies, at least six of eight targets must rank first at each size, non-compatible conflict/premise targets with ten component siblings must rank first or second, and high-frequency or unknown terms must open zero bodies. The corpus is synthetic and model-free; this is not a token-savings measurement or a holdout-quality claim.

## Static prompt-character check

`tests/context-v1/test_distribution_proof.py` computes this observation from all committed Codex plugin manifests:

```bash
python -m pytest -q tests/context-v1/test_distribution_proof.py::DistributionProofTests::test_public_trust_contract_matches_the_release_surface
```

For release set 0.13.0, the recorded static prompt material moved from 3,147 to 2,021 characters, a 35.8% character reduction. This is a character-count observation and not a token-savings measurement.

## Historical host experiment

Historical, one-repeat Codex experiment; raw evidence is not published in this repository. The original local sources were `value-validation-v4/RESULTS.ko.md`, `protocol.v4.json`, and the `evidence/{r1,scale200}` scorecards on the former `task/value-validation-v4` worktree. Because the corpus and raw runs are unavailable here, these figures are context, not independently reproducible release claims.

- At N=0, mean input tokens across three session types were approximately 200K for 0.13.0, 78K for lean v6, and 77K for the adr-lite comparison.
- At N=200, mean input tokens for semantic-recall sessions were 85K for lean v6 and 342K for adr-lite.
- The denominators differ: the N=0 figures cover all three session types, while the N=200 figures cover semantic recall only.
- The experiment used one repeat, eight scenarios at N=0, and four scenarios at N=200 on Codex. Lean v6 held all measured true conflicts, while its N=0 no-signal result was seven of eight.
- Claude Code behavior, repeat variance, and SNAP/DOCUMENT resume value were not measured.

The safe public conclusion is narrower than “always saves tokens”: Context Plugins pays a bounded static-instruction cost, avoids extra context calls and body reads on a healthy no-signal path, and has shown a cost and retrieval advantage as context grew in one historical experiment. Repetition and current-host generalization remain unverified.
