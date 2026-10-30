"""Replay saved observations into a fresh directory, without source requests."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from analyze import execute

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=["development", "all"], default="all")
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.is_relative_to(ROOT), "Use an output outside the checkout"
    output.mkdir(parents=True, exist_ok=False)
    source_paths = list((ROOT / "data").glob("*.json")) + [ROOT / "data/cohort.csv"]
    before = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source_paths
    }
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        check=True,
    )
    if args.phase == "all":
        subprocess.run([sys.executable, "src/freeze_proof.py"], cwd=ROOT, check=True)
    fields = 0
    phases = (
        ["development"] if args.phase == "development" else ["development", "final"]
    )
    for phase in phases:
        target = output / phase
        execute(ROOT, phase, target)
        for name in ["actions.json", "summary.json"]:
            expected = ROOT / "results" / phase / name
            assert (target / name).read_bytes() == expected.read_bytes(), name

            def count(value):
                if isinstance(value, dict):
                    return sum(count(v) for v in value.values())
                if isinstance(value, list):
                    return sum(count(v) for v in value)
                return 1

            fields += count(json.loads(expected.read_text(encoding="utf-8")))
        subprocess.run(
            [
                sys.executable,
                "src/verify_captures.py",
                "--phase",
                phase,
                "--output",
                str(target / "independent_check.json"),
            ],
            cwd=ROOT,
            check=True,
        )
    for name, action in [
        ("repeated-not-found", "review_doi_candidate"),
        ("transient-not-found", "http_response_observed"),
        ("accepted-incomplete", "unknown"),
    ]:
        result = json.loads(
            subprocess.check_output(
                [sys.executable, "src/triage_cli.py", "examples/" + name + ".json"],
                cwd=ROOT,
                text=True,
            )
        )
        assert (
            result["status"] == "OFFLINE_TRIAGE"
            and result["conservative_action"] == action
            and result["data_file_availability"] == "not_tested"
        )
    assert before == {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source_paths
    }
    report = dict(
        status="PASS_OFFLINE_CAPTURED_REPLAY",
        phases=phases,
        reproduced_JSON_scalar_fields=fields,
        authored_CLI_examples=3,
        source_files_unchanged=len(before),
        source_network_requests=0,
        qualifications="Captured response replay only. No new availability observation, raw private source recapture or human validation.",
    )
    (output / "replay_receipt.json").write_bytes(
        (json.dumps(report, indent=2) + "\n").encode()
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
