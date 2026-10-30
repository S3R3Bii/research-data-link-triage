"""Check recorded provenance and recompute actions without importing rule code."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def stamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return parsed.astimezone(timezone.utc)


def clean(url):
    value = urlsplit(url)
    return urlunsplit((value.scheme, value.netloc.split("@")[-1], value.path, "", ""))


def category(row):
    code = row["terminal_http_status"]
    if row.get("transport_error_type"):
        return "transport_failure"
    if row.get("censor_reason") not in [
        None,
        "non_html_response_not_consumed",
        "rate_limit_observed",
    ]:
        return "censored_or_not_observed"
    if not row["request_made"]:
        return "not_observed"
    if code is None:
        return "incomplete_without_status"
    if code == 202:
        return "accepted_not_completed"
    if code == 204:
        return "no_content_response"
    if 200 <= code < 300:
        return "http_response_observed"
    if code in (404, 410):
        return "not_found_response"
    if code in (401, 403):
        return "access_restricted"
    if code == 429:
        return "rate_limited"
    if 500 <= code < 600:
        return "server_failure"
    if 300 <= code < 400:
        return "redirect_incomplete"
    return "other_response"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["development", "final"], required=True)
    parser.add_argument("--private-observations", type=Path)
    parser.add_argument("--private-references", type=Path)
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checks = 0

    def require(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(message)

    with (ROOT / "data/cohort.csv").open(newline="", encoding="utf-8") as source:
        cohort = [r for r in csv.DictReader(source) if r["phase"] == args.phase]
    items = {r["item_id"]: r for r in cohort}
    require(len(cohort) == len(items) == 11, "cohort identity")
    cohort_hash = hashlib.sha256((ROOT / "data/cohort.csv").read_bytes()).hexdigest()
    snapshots = []
    inputs = {}
    for number in [1, 2]:
        path = (
            ROOT / "data" / ("observations_" + args.phase + "_" + str(number) + ".json")
        )
        if not path.exists():
            require(args.capture_only and number == 2, "both snapshots required")
            continue
        snap = read(path)
        inputs[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        require(snap["cohort_sha256"] == cohort_hash, "cohort hash")
        require(
            snap["phase"] == args.phase and snap["number"] == number, "phase/number"
        )
        require(
            [r["item_id"] for r in snap["observations"]] == list(items),
            "complete ordered snapshot",
        )
        for row in snap["observations"]:
            item = items[row["item_id"]]
            require(
                row["dataset_doi"] == item["dataset_doi"]
                and row["role"] == item["role"],
                "identity",
            )
            require(
                stamp(row["started_utc"]) <= stamp(row["completed_utc"]),
                "capture chronology",
            )
            require(
                row["data_file_availability"] == "not_tested"
                and row["data_file_body_downloaded"] is False,
                "file boundary",
            )
            require(type(row["request_made"]) is bool, "request state")
            previous = stamp(row["started_utc"])
            for step in row["chain"]:
                url = urlsplit(step["url"])
                host = url.hostname or ""
                allowed = [
                    "doi.org",
                    "dx.doi.org",
                    "zenodo.org",
                    "datadryad.org",
                    "figshare.com",
                ]
                require(
                    url.scheme == "https"
                    and any(host == h or host.endswith("." + h) for h in allowed),
                    "public allowed origin",
                )
                require(
                    not url.query
                    and not url.fragment
                    and not url.username
                    and not url.password,
                    "sanitized receipt",
                )
                require(
                    previous
                    <= stamp(step["started_utc"])
                    <= stamp(step["completed_utc"])
                    <= stamp(row["completed_utc"]),
                    "step chronology",
                )
                previous = stamp(step["completed_utc"])
                require(0 <= step["stored_bytes"] <= 262144, "body bound")
                if step.get("request_started_utc"):
                    require(
                        stamp(step["started_utc"])
                        <= stamp(step["request_started_utc"])
                        <= stamp(step["completed_utc"]),
                        "GET-start time",
                    )
            require(
                not row["chain"]
                or row["terminal_http_status"] == row["chain"][-1]["http_status"],
                "terminal status",
            )
            if args.private_observations:
                folder = (
                    args.private_observations
                    / args.phase
                    / ("probe" + str(number))
                    / row["item_id"]
                )
                transcript_path = folder / "transcript.json"
                require(
                    hashlib.sha256(transcript_path.read_bytes()).hexdigest()
                    == row["private_transcript_sha256"],
                    "private transcript hash",
                )
                raw = read(transcript_path)
                expected = dict(row)
                expected.pop("private_transcript_sha256")
                require(raw["public_record"] == expected, "captured public record")
                require(len(raw["raw_steps"]) == len(row["chain"]), "chain length")
                for native, public in zip(raw["raw_steps"], row["chain"]):
                    body = (folder / native["body_file"]).read_bytes()
                    require(
                        hashlib.sha256(body).hexdigest()
                        == native["body_sha256"]
                        == public["body_sha256"],
                        "body hash",
                    )
                    require(
                        len(body) == native["stored_bytes"] == public["stored_bytes"],
                        "body length",
                    )
                    require(
                        clean(native["url"]) == public["url"]
                        and native.get("http_status") == public["http_status"],
                        "native URL/status",
                    )
                    headers = native.get("headers", {})
                    require(
                        headers.get("Content-Type") == public["content_type"]
                        and headers.get("Retry-After") == public["retry_after"],
                        "native safe headers",
                    )
        snapshots.append(snap)
    refs_path = ROOT / "data" / ("references_" + args.phase + ".json")
    refs = read(refs_path)["records"]
    inputs[refs_path.relative_to(ROOT).as_posix()] = hashlib.sha256(
        refs_path.read_bytes()
    ).hexdigest()
    require([r["item_id"] for r in refs] == list(items), "complete ordered references")
    for ref, first in zip(refs, snapshots[0]["observations"]):
        item = items[ref["item_id"]]
        require(ref["url"] == clean(ref["url"]), "sanitized reference URL")
        require(
            ref["dataset_doi"] == item["dataset_doi"] and ref["role"] == item["role"],
            "reference identity",
        )
        require(
            ref["reference_label"]
            in ["available_landing", "not_found_control", "unknown"],
            "reference state",
        )
        require(
            ref["inspector"]
            == "agent_browser_inspection_not_independent_human_validation",
            "reference qualification",
        )
        require(
            abs(
                (
                    stamp(ref["reference_utc"]) - stamp(first["completed_utc"])
                ).total_seconds()
                - ref["probe_to_reference_seconds"]
            )
            < 1e-6,
            "reference timing",
        )
        if args.private_references:
            path = args.private_references / args.phase / ref["evidence_file"]
            require(
                hashlib.sha256(path.read_bytes()).hexdigest() == ref["evidence_sha256"],
                "DOM hash",
            )
            text = path.read_text(encoding="utf-8")
            require(ref["dataset_doi"] in text.lower(), "visible DOI")
            if ref["reference_label"] == "available_landing":
                require(
                    item["role"] == "real_dataset"
                    and "dataset" in text.lower()
                    and "Download" in text,
                    "landing context",
                )
            elif ref["reference_label"] == "not_found_control":
                require(
                    item["role"] == "authored_control" and "DOI Not Found" in text,
                    "negative control context",
                )
    if not args.capture_only:
        first, second = snapshots
        first_hash = inputs["data/observations_" + args.phase + "_1.json"]
        require(second["first_snapshot_sha256"] == first_hash, "first snapshot binding")
        latest_first = max(stamp(r["completed_utc"]) for r in first["observations"])
        require(
            min(stamp(r["started_utc"]) for r in second["observations"]) >= latest_first
            and (
                min(stamp(r["started_utc"]) for r in second["observations"])
                - latest_first
            ).total_seconds()
            >= 1200,
            "phase-wide actual repeat gate",
        )
        actions = read(ROOT / "results" / args.phase / "actions.json")
        report = read(ROOT / "results" / args.phase / "summary.json")
        require([r["item_id"] for r in actions] == list(items), "all actions")
        for a, b, result, ref in zip(
            first["observations"], second["observations"], actions, refs
        ):
            ca, cb = category(a), category(b)
            gap = (stamp(b["started_utc"]) - stamp(a["completed_utc"])).total_seconds()
            naive = (
                "broken_alert"
                if a.get("transport_error_type")
                or (
                    a["request_made"]
                    and a["terminal_http_status"] is not None
                    and not 200 <= a["terminal_http_status"] < 300
                )
                else (
                    "unknown_no_alert" if not a["request_made"] else "no_broken_alert"
                )
            )
            action = "unknown"
            reason = cb
            if gap < 1200:
                reason = "repeat_too_early"
            elif ca == cb == "not_found_response":
                action, reason = (
                    "review_doi_candidate",
                    "two_spaced_not_found_responses",
                )
            elif cb == "http_response_observed":
                action = "http_response_observed"
                reason = (
                    "response_changed_from_not_found"
                    if ca == "not_found_response"
                    else "later_http_response_only"
                )
            elif ca == cb == "accepted_not_completed":
                reason = "accepted_processing_not_completed"
            for key, expected in dict(
                naive_action=naive,
                conservative_action=action,
                reason=reason,
                first_response_class=ca,
                second_response_class=cb,
                repeat_gap_seconds=gap,
                reference_label=ref["reference_label"],
                first_status=a["terminal_http_status"],
                second_status=b["terminal_http_status"],
            ).items():
                require(result[key] == expected, "independent action: " + key)
        counts = dict(sorted(Counter(r["reference_label"] for r in refs).items()))
        gate = (
            counts.get("available_landing", 0) >= 4
            and counts.get("not_found_control", 0) == 3
        )
        require(
            report["reference_counts"] == counts
            and report["reference_gate"] == ("PASS" if gate else "FAIL"),
            "reference gate",
        )
        for field, positive in [
            ("naive_action", "broken_alert"),
            ("conservative_action", "review_doi_candidate"),
        ]:
            if not gate:
                require(report["comparisons"] is None, "no unsupported comparison")
                continue
            metric = report["comparisons"][field]
            avail = [r for r in actions if r["reference_label"] == "available_landing"]
            controls = [
                r for r in actions if r["reference_label"] == "not_found_control"
            ]
            tp = sum(r[field] == positive for r in controls)
            fp = sum(r[field] == positive for r in avail)
            unknown = sum(r["reference_label"] == "unknown" for r in actions)
            expected = dict(
                total=len(actions),
                reference_evaluable=len(actions) - unknown,
                reference_unknown=unknown,
                real_available_reference=len(avail),
                authored_not_found_controls=len(controls),
                tp_control=tp,
                fn_control=len(controls) - tp,
                fp_available_reference=fp,
                tn_available_reference=len(avail) - fp,
                alert_count=sum(r[field] == positive for r in actions),
                deferred_unknown=sum(
                    r[field] in ["unknown", "unknown_no_alert", "await_repeat"]
                    for r in actions
                ),
                action_counts=dict(sorted(Counter(r[field] for r in actions).items())),
            )
            for key, value in expected.items():
                require(metric[key] == value, "independent metric: " + key)
            for key, num, den in [
                ("apparent_false_alert_fraction", fp, len(avail)),
                ("control_recall", tp, len(controls)),
                ("conditional_precision", tp, tp + fp),
            ]:
                rate = metric[key]
                require(
                    rate["numerator"] == num and rate["denominator"] == den,
                    "exact rate denominator",
                )
                require(
                    rate["value"] == (float(Fraction(num, den)) if den else None),
                    "independent Fraction rate",
                )
        for number, key in [(0, "first_status_counts"), (1, "second_status_counts")]:
            require(
                report[key]
                == dict(
                    sorted(
                        Counter(
                            str(r["terminal_http_status"])
                            for r in snapshots[number]["observations"]
                        ).items()
                    )
                ),
                "status counts",
            )
        gaps = [
            (stamp(b["started_utc"]) - stamp(a["completed_utc"])).total_seconds()
            for a, b in zip(first["observations"], second["observations"])
        ]
        require(
            report["minimum_repeat_gap_seconds"] == min(gaps)
            and report["maximum_repeat_gap_seconds"] == max(gaps),
            "gap extrema",
        )
        for name, digest in report["input_sha256"].items():
            require(
                hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest,
                "analysis input hash",
            )
    result = dict(
        status="PASS",
        phase=args.phase,
        capture_only=args.capture_only,
        conditions=checks,
        private_originals_checked=bool(args.private_observations),
        private_DOM_checked=bool(args.private_references),
        input_sha256=inputs,
        scope="Finite stored provenance and independently recomputed arithmetic; not data-file access or human expert validation",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
