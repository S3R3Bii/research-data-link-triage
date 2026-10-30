"""Bind manuscript values directly to captured states and reference times."""

import ast, csv, hashlib, json, re
from datetime import datetime
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    text = (ROOT / "paper/main.tex").read_text(encoding="utf-8")
    bindings = read("data/paper_number_map.json")
    source = read("data/input_receipt.json")
    native = read("data/metadata_original_verification.json")
    expected = {
        "MSourceItems": native["source_items"],
        "MCandidates": native["candidate_pairs"],
        "MLookups": native["metadata_lookups"],
        "MRealPairs": native["selected_pairs"],
        "MControls": source["authored_controls"],
        "MSourceFiles": native["source_files_unchanged"],
        "MNativeChecks": native["conditions_checked"],
        "MDevChecks": read("data/development_verification.json")["conditions"],
        "MFinalChecks": read("data/final_verification.json")["conditions"],
        "MDeps": len(read("data/dependency_freeze.json")["files"]),
        "MCaptureBytes": read("data/capture_budget_verification.json")["total_bytes"],
        "MFigureValues": read("figures/verification.json")["figure_values"],
        "MCoordinates": read("figures/verification.json")["SVG_coordinates"],
    }
    all_observations = []
    for prefix, phase in [("MDev", "development"), ("MFinal", "final")]:
        first = read("data/observations_" + phase + "_1.json")["observations"]
        second = read("data/observations_" + phase + "_2.json")["observations"]
        refs = read("data/references_" + phase + ".json")["records"]
        all_observations += first + second
        expected[prefix + "Real"] = sum(
            r["reference_label"] == "available_landing" for r in refs
        )
        expected[prefix + "Controls"] = sum(
            r["reference_label"] == "not_found_control" for r in refs
        )
        naive = review = false_naive = false_review = unknown = 0
        gaps = []
        reference_gaps = []
        for a, b, ref in zip(first, second, refs):
            gap = (stamp(b["started_utc"]) - stamp(a["completed_utc"])).total_seconds()
            gaps.append(gap)
            flag = bool(a["transport_error_type"]) or (
                a["request_made"]
                and a["terminal_http_status"] is not None
                and not 200 <= a["terminal_http_status"] < 300
            )
            candidate = (
                gap >= 1200
                and a["terminal_http_status"] in [404, 410]
                and b["terminal_http_status"] in [404, 410]
                and a["censor_reason"] is None
                and b["censor_reason"] is None
            )
            naive += flag
            review += candidate
            false_naive += flag and ref["reference_label"] == "available_landing"
            false_review += candidate and ref["reference_label"] == "available_landing"
            unknown += (
                a["role"] == "real_dataset"
                and a["terminal_http_status"] == b["terminal_http_status"] == 202
            )
            reference_gaps.append(
                (
                    stamp(ref["reference_utc"]) - stamp(a["completed_utc"])
                ).total_seconds()
            )
        expected.update(
            {
                prefix + "NaiveAlerts": naive,
                prefix + "ReviewCandidates": review,
                prefix + "NaiveFalse": false_naive,
                prefix + "ConservativeFalse": false_review,
                prefix + "Unknown": unknown,
                prefix + "RepeatMin": min(gaps),
                prefix + "RepeatMax": max(gaps),
                prefix + "ReferenceMin": min(reference_gaps),
                prefix + "ReferenceMax": max(reference_gaps),
            }
        )
    expected.update(
        MObservedItems=len(all_observations) // 2,
        MObservations=len(all_observations),
        MRequestSteps=sum(len(r["chain"]) for r in all_observations),
        MRealResponses=sum(r["role"] == "real_dataset" for r in all_observations),
        MControlResponses=sum(
            r["role"] == "authored_control" for r in all_observations
        ),
        MExactGETStarts=sum(
            step.get("request_started_utc") is not None
            for r in all_observations
            for step in r["chain"]
        ),
    )
    assert set(expected) == set(bindings)
    pdf_path = ROOT / "paper/manuscript.pdf"
    pdf = PdfReader(pdf_path)
    assert len(pdf.pages) == 6
    pages = [page.extract_text() or "" for page in pdf.pages]
    flat = "\n".join(pages)
    checks = 0
    for name, value in expected.items():
        actual = bindings[name]
        assert actual["value"] == value, name
        checks += 1
        display = (
            f"{value:.3f}"
            if isinstance(value, float)
            else (f"{value:,}" if name == "MCaptureBytes" else str(value))
        )
        assert (
            actual["display"] == display
            and "\\newcommand{\\" + name + "}{" + display + "}" in text
        ), name
        checks += 1
        assert display in flat, (name, display)
        checks += 1
    for row in all_observations:
        assert row["terminal_http_status"] == (
            202 if row["role"] == "real_dataset" else 404
        )
        checks += 1
        if row["role"] == "real_dataset":
            assert row["chain"][-1]["stored_bytes"] == 0
            checks += 1
    with (ROOT / "data/cohort.csv").open(newline="", encoding="utf-8") as stream:
        cohort = list(csv.DictReader(stream))
    real = [r for r in cohort if r["role"] == "real_dataset"]
    assert (
        len({r["dataset_doi"] for r in real})
        == len({r["parent_doi"] for r in real})
        == 16
    )
    assert (
        all(
            r["dataset_doi"].startswith("10.6084/")
            and r["registry_landing_url_sanitized"].startswith(
                "https://karger.figshare.com/"
            )
            for r in real
        )
        if "registry_landing_url_sanitized" in real[0]
        else all(
            r["dataset_doi"].startswith("10.6084/")
            and r["registry_url_sanitized"].startswith("https://karger.figshare.com/")
            for r in real
        )
    )
    checks += 2
    fixture_count = sum(
        isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
        for path in (ROOT / "tests").glob("test_*.py")
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )
    assert fixture_count == 36 and "Thirty-six fixture methods" in text
    checks += 1
    for phrase in [
        "no observed false-alert reduction",
        "not peer reviewed",
        "2019 publication-date",
        "10.5061, 10.6084 and 10.5281",
        "twenty-minute phase-wide",
        "256 KiB",
        "100 MiB",
        "HTTP 202",
        "404/410",
        "Agent inspection is not independent human validation",
        "Automatic browser preview traffic is not audited",
        "exact GET initiation was not separately logged",
        "after the first development observations",
        "before comparison/final probes",
        "file availability remains untested",
        "Syntax-invalid cases remain fixtures",
        "No model is trained",
        "Azure optimization",
    ]:
        assert phrase in text, phrase
        checks += 1
    build = read("paper/build_receipt.json")
    assert (
        build["status"] == "PASS_COMPILE_AND_SIX_PAGE_VISUAL_REVIEW"
        and build["layout_warnings"] == []
    )
    for name in ["paper/main.tex", "paper/manuscript.pdf"]:
        assert (
            hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            == build["artifact_sha256"][name]
        )
        checks += 1
    for name in ["captured_responses", "conditional_comparison", "observation_timing"]:
        assert "images/" + name + ".pdf" in text
        assert (ROOT / "paper/images" / (name + ".pdf")).read_bytes() == (
            ROOT / "figures" / (name + ".pdf")
        ).read_bytes()
        checks += 2
    assert all(len(page) > 1200 for page in pages)
    result = dict(
        status="PASS_SOURCE_BOUND_MANUSCRIPT",
        bound_measured_displays=len(expected),
        conditions=checks,
        pages=len(pages),
        PDF_text_extracted=True,
        visual_page_inspection="Separate owner inspection of all six 95dpi rendered pages",
        paper_sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        source_sha256=hashlib.sha256(
            (ROOT / "paper/main.tex").read_bytes()
        ).hexdigest(),
        limits="Finite source/text/retained-build checks; public CI does not compile TeX or access private logs. Agent references and source file availability remain qualified.",
    )
    (ROOT / "paper_verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
