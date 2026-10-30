"""Summarize captured states without converting unknowns into link failures."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from rules import compare

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fraction(numerator, denominator):
    return dict(
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator if denominator else None,
    )


def summarize(rows, field):
    evaluable = [r for r in rows if r["reference_label"] != "unknown"]
    positive = "broken_alert" if field == "naive_action" else "review_doi_candidate"
    available = [r for r in evaluable if r["reference_label"] == "available_landing"]
    controls = [r for r in evaluable if r["reference_label"] == "not_found_control"]
    tp = sum(r[field] == positive for r in controls)
    fp = sum(r[field] == positive for r in available)
    return dict(
        semantics="broken alert" if field == "naive_action" else "DOI review candidate",
        total=len(rows),
        reference_evaluable=len(evaluable),
        reference_unknown=len(rows) - len(evaluable),
        real_available_reference=len(available),
        authored_not_found_controls=len(controls),
        tp_control=tp,
        fn_control=len(controls) - tp,
        fp_available_reference=fp,
        tn_available_reference=len(available) - fp,
        alert_count=sum(r[field] == positive for r in rows),
        deferred_unknown=sum(
            r[field] in ["unknown", "unknown_no_alert", "await_repeat"] for r in rows
        ),
        apparent_false_alert_fraction=fraction(fp, len(available)),
        control_recall=fraction(tp, len(controls)),
        conditional_precision=fraction(tp, tp + fp),
        action_counts=dict(sorted(Counter(r[field] for r in rows).items())),
    )


def execute(root, phase, output):
    cohort_path = root / "data/cohort.csv"
    with cohort_path.open(newline="", encoding="utf-8") as source:
        cohort = [r for r in csv.DictReader(source) if r["phase"] == phase]
    first_path = root / "data" / ("observations_" + phase + "_1.json")
    second_path = root / "data" / ("observations_" + phase + "_2.json")
    reference_path = root / "data" / ("references_" + phase + ".json")
    first, second, references = map(load, [first_path, second_path, reference_path])
    ids = [r["item_id"] for r in cohort]
    assert len(ids) == len(set(ids)) == 11
    sha = hashlib.sha256(cohort_path.read_bytes()).hexdigest()
    for snapshot in [first, second]:
        assert snapshot["phase"] == phase and snapshot["cohort_sha256"] == sha
        assert [r["item_id"] for r in snapshot["observations"]] == ids
    assert (
        second["first_snapshot_sha256"]
        == hashlib.sha256(first_path.read_bytes()).hexdigest()
    )
    assert [r["item_id"] for r in references["records"]] == ids
    rows = []
    for item, a, b, ref in zip(
        cohort, first["observations"], second["observations"], references["records"]
    ):
        for record in [a, b, ref]:
            assert (
                record["dataset_doi"] == item["dataset_doi"]
                and record["role"] == item["role"]
            )
        label = ref["reference_label"]
        assert label in ["available_landing", "not_found_control", "unknown"]
        assert label != "not_found_control" or item["role"] == "authored_control"
        result = compare(a, b)
        rows.append(
            dict(
                result,
                reference_label=label,
                reference_utc=ref["reference_utc"],
                first_status=a["terminal_http_status"],
                second_status=b["terminal_http_status"],
            )
        )
    counts = Counter(r["reference_label"] for r in rows)
    gate = counts["available_landing"] >= 4 and counts["not_found_control"] == 3
    report = dict(
        phase=phase,
        status="CAPTURED_COMPARISON_PENDING_INDEPENDENT_CHECK",
        reference_gate="PASS" if gate else "FAIL",
        reference_counts=dict(sorted(counts.items())),
        source_family="All sixteen real datasets are Karger Figshare; not a multi-repository evaluation",
        comparisons=(
            {
                key: summarize(rows, key)
                for key in ["naive_action", "conservative_action"]
            }
            if gate
            else None
        ),
        minimum_repeat_gap_seconds=min(r["repeat_gap_seconds"] for r in rows),
        maximum_repeat_gap_seconds=max(r["repeat_gap_seconds"] for r in rows),
        first_status_counts=dict(
            sorted(Counter(str(r["first_status"]) for r in rows).items())
        ),
        second_status_counts=dict(
            sorted(Counter(str(r["second_status"]) for r in rows).items())
        ),
        input_sha256={
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [cohort_path, first_path, second_path, reference_path]
        },
        qualifications=[
            "References are agent-inspected browser pages, not independent human labels or same-instant truth.",
            "Controls are authored examples, not discovered broken scholarly links.",
            "Binary non-alert counts include deferred unknowns; do not interpret them as verified available data.",
            "HTTP 202 records an accepted request without completed content; files were never opened.",
            "No population error rate, actual workload saving, repair or scientific claim verification.",
        ],
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "actions.json").write_bytes((json.dumps(rows, indent=2) + "\n").encode())
    (output / "summary.json").write_bytes(
        (json.dumps(report, indent=2, allow_nan=False) + "\n").encode()
    )
    print(
        json.dumps(
            dict(
                phase=phase,
                reference_gate=report["reference_gate"],
                comparisons=report["comparisons"],
                minimum_repeat_gap_seconds=report["minimum_repeat_gap_seconds"],
            )
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["development", "final"], required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    execute(ROOT, args.phase, args.output or ROOT / "results" / args.phase)
