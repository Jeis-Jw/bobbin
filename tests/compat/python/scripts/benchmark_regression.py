#!/usr/bin/env python3
"""Compare 0.15.0 and Bobbin on identical synthetic records, without an LLM.

Pass an extracted baseline checkout (git archive is sufficient). All generated
records and settings stay in a temporary directory. Timings include Python CLI
startup, use warmed filesystem caches, and alternate versions to limit drift.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def paths(root, bobbin):
    package = root / "plugins"
    return {
        "core": package / ("bobbin" if bobbin else "context-core") / "skills/context/scripts/context_cli.py",
        "check": package / ("bobbin" if bobbin else "context-decision") / "skills/decision/scripts/decision_cli.py",
        "record": package / ("bobbin" if bobbin else "context-decision") / "skills/decision/scripts/decision_workflow.py",
        "init": package / ("bobbin" if bobbin else "context-decision") / "skills/init/scripts" / ("bobbin_init.py" if bobbin else "decision_init.py"),
    }


def run(path, args, cwd):
    env = dict(os.environ)
    env.pop("BOBBIN_PROJECT_ROOT", None)
    started = time.perf_counter()
    completed = subprocess.run([sys.executable, str(path), *args, "--json"], cwd=cwd, env=env, capture_output=True, text=True)
    elapsed = (time.perf_counter() - started) * 1000
    if completed.returncode:
        raise RuntimeError(completed.stdout + completed.stderr)
    payload = json.loads(completed.stdout)
    assert payload["ok"], payload
    return elapsed, payload["result"]


def summary(samples):
    return {"median_ms": round(statistics.median(samples), 3),
            "p95_ms": round(sorted(samples)[math.ceil(len(samples) * .95) - 1], 3),
            "samples_ms": [round(value, 3) for value in samples]}


def record_args(number, mode):
    args = ["record", "--host", "codex", "--inline", "--title", f"Offline cache {number}",
            "--summary", "Keep the cache available offline.", "--scope", "benchmark", "--decision-key", f"cache-{number}",
            "--commitment-evidence", "User explicitly selected an offline cache.",
            "--sec-decision", "Use an embedded local cache.", "--sec-rationale", "The product must work offline.",
            "--sec-alternatives", "A remote-only cache was rejected.",
            "--attest-explicit-choice", "--attest-scope-identified", "--attest-commitment-present"]
    if mode == "explicit":
        args += ["--approved"]
    else:
        args += ["--approval-source", "policy"]
        if mode == "adaptive":
            args += ["--policy-decision", "record", "--policy-reason", "The user's choice, scope and evidence are settled."]
    return args


def benchmark(baseline, candidate, repeats):
    versions = {"baseline": paths(baseline, False), "bobbin": paths(candidate, True)}
    fixture = load("benchmark_fixture", baseline / "tests/context-v1/test_token_io_evidence.py")
    cores = {"baseline": fixture.context_cli, "bobbin": load("benchmark_bobbin_core", versions["bobbin"]["core"])}
    report = {"python": platform.python_version(), "platform": platform.platform(), "repeats": repeats,
              "cache": "warm; 3 untimed CLI warmups per workload and version",
              "corpus": {"current": 4100, "history": 1000}, "reads": {}, "writes": {}, "io": {}}
    with tempfile.TemporaryDirectory(prefix="bobbin-benchmark-") as temporary:
        root = Path(temporary)
        vault = root / "corpus"
        fixture.SyntheticRepository(vault)
        project = root / "read-project"
        (project / ".bobbin").mkdir(parents=True)
        (project / ".bobbin/config.json").write_text(json.dumps({"schema": "bobbin-project/v1", "features": ["decision"],
            "approval": {"mode": "explicit"}, "vault": str(vault)}))
        working = {"baseline": vault, "bobbin": project}
        workloads = {
            "metadata_hit": ("core", ["recall", "--query", "probe0001", "--area", "observation"]),
            "metadata_miss": ("core", ["recall", "--query", "quasar-zeppelin", "--area", "observation"]),
            "cross_area": ("core", ["recall", "--query", "probe0001"]),
            "selected_pack": ("core", ["recall", "--query", "probe0001", "--area", "decision", "--pack"]),
            "decision_check": ("check", ["check", "--statement", "probe0001"]),
        }
        for label, (entry, args) in workloads.items():
            samples = {key: [] for key in versions}
            for iteration in range(repeats + 3):
                results = {}
                for key in (list(versions) if iteration % 2 else list(reversed(versions))):
                    elapsed, result = run(versions[key][entry], args, working[key])
                    results[key] = result["comparison_input"]["current"] if entry == "check" else result
                    if iteration >= 3:
                        samples[key].append(elapsed)
                assert results["baseline"] == results["bobbin"], f"Behavior changed: {label}"
            report["reads"][label] = {key: summary(values) for key, values in samples.items()}

        for label, kwargs in {"metadata_hit": {"query": "probe0001", "areas": ["observation"]},
                              "metadata_miss": {"query": "quasar-zeppelin", "areas": ["observation"]},
                              "cross_area": {"query": "probe0001"},
                              "selected_pack": {"query": "probe0001", "areas": ["decision"], "pack": True}}.items():
            measured = {}
            for key, core in cores.items():
                metrics = core.IOMetrics()
                if key == "bobbin":
                    with core._config_call("project_environment", project):
                        result = core.recall_repository(vault, metrics=metrics, **kwargs)
                else:
                    result = core.recall_repository(vault, metrics=metrics, **kwargs)
                measured[key] = {"metrics": vars(metrics), "returned": result["returned"],
                                 "result_bytes": len(core.canonical_json(result).encode())}
            assert measured["baseline"] == measured["bobbin"], f"I/O contract changed: {label}"
            report["io"][label] = measured["bobbin"]

        lanes = [("baseline", "explicit"), ("bobbin", "explicit"), ("bobbin", "auto"), ("bobbin", "adaptive")]
        for key, mode in lanes:
            target = root / f"write-{key}-{mode}"
            target.mkdir()
            selected = versions[key]
            if key == "baseline":
                run(selected["core"], ["init", "--host", "codex"], target)
                run(selected["init"], ["--host", "codex", "--core-cli", str(selected["core"])], target)
            else:
                run(selected["init"], ["--host", "codex", "--approval-mode", mode], target)
            report["writes"][f"{key}_{mode}"] = []
        for number in range(repeats + 3):
            order = lanes[number % len(lanes):] + lanes[:number % len(lanes)]
            for key, mode in order:
                target = root / f"write-{key}-{mode}"
                elapsed, result = run(versions[key]["record"], record_args(number, mode), target)
                assert result["applied"] and result["receipt_removed"], result
                if key == "bobbin":
                    assert result["authorization"]["source"] == ("user" if mode == "explicit" else "policy")
                if number >= 3:
                    report["writes"][f"{key}_{mode}"].append(elapsed)
        for key, mode in lanes:
            target = root / f"write-{key}-{mode}"
            _, check = run(versions[key]["core"], ["refresh", "--check"], target)
            assert check["ok"], check
            assert len(list((target / "context/decision").glob("Offline-cache-*.md"))) == repeats + 3
        report["writes"] = {key: summary(values) for key, values in report["writes"].items()}
    report["source_sha256"] = {key: {entry: hashlib.sha256(path.read_bytes()).hexdigest() for entry, path in value.items()}
                               for key, value in versions.items()}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--repeats", type=int, default=31)
    args = parser.parse_args()
    if args.repeats < 5:
        parser.error("Use at least 5 measured repeats.")
    print(json.dumps(benchmark(args.baseline.resolve(), args.candidate.resolve(), args.repeats), indent=2))


if __name__ == "__main__":
    main()
