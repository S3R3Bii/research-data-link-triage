"""Use DuckDB's JSON parser and SQL to reconstruct the exact source cohort."""

import argparse, csv, hashlib, json, re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import duckdb

ROOT = Path(__file__).resolve().parents[1]


def normalize(native):
    if not isinstance(native, str):
        raise ValueError("Nonstring identifier")
    value = native.strip()
    if value[:8].lower() == "https://":
        parsed = urlsplit(value)
        if (
            parsed.netloc.lower()
            not in ["doi.org", "dx.doi.org", "doi.org:443", "dx.doi.org:443"]
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ambiguous wrapper")
        value = parsed.path.lstrip("/")
    if (
        not value.isascii()
        or len(value) > 240
        or any(c in value for c in "? #%@")
        or any(ord(c) < 33 or ord(c) == 127 for c in value)
    ):
        raise ValueError("Unsupported representation")
    if not re.match(r"^10\.[0-9]{4,9}/.+$", value):
        raise ValueError("Unsupported DOI")
    return value.lower()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    private = args.private_root
    connection = duckdb.connect(":memory:")
    receipts = json.loads(
        (private / "acquisition-receipts.json").read_text(encoding="utf-8")
    )
    checks = 0
    for receipt in receipts:
        assert (
            hashlib.sha256((private / receipt["body_file"]).read_bytes()).hexdigest()
            == receipt["sha256"]
        )
        checks += 1
    latest = {r["key"]: r for r in receipts}
    cross = private / latest["crossref-cohort"]["body_file"]
    count = connection.execute(
        "select json_array_length(json_extract(json,'$.message.items')) from read_json_objects(?)",
        [str(cross)],
    ).fetchone()[0]
    assert count == 100
    checks += 1
    items = connection.execute(
        "select cast(w.key as integer),json_extract_string(w.value,'$.DOI'),json_extract_string(w.value,'$.type'),json_extract_string(w.value,'$.published.date-parts[0][0]') from read_json_objects(?) t,json_each(t.json,'$.message.items') w",
        [str(cross)],
    ).fetchall()
    assert len(items) == 100 and all(
        kind == "journal-article" and year == "2019" for i, doi, kind, year in items
    )
    checks += 100
    native = connection.execute(
        "select cast(w.key as integer),cast(r.key as integer),json_extract_string(w.value,'$.DOI'),json_extract_string(r.value,'$.id'),json_extract_string(r.value,'$.id-type'),json_extract_string(r.value,'$.\"asserted-by\"') from read_json_objects(?) t,json_each(t.json,'$.message.items') w,json_each(w.value,'$.relation.\"is-supplemented-by\"') r",
        [str(cross)],
    ).fetchall()
    candidates = []
    for i, j, parent, target, id_type, asserted in native:
        if (
            id_type is None
            or id_type.lower() != "doi"
            or asserted not in ["subject", "object"]
        ):
            continue
        try:
            p, d = normalize(parent), normalize(target)
        except ValueError:
            continue
        if d.split("/")[0] in ["10.5061", "10.6084", "10.5281"]:
            candidates.append((p, d, i, j, asserted))
    candidates.sort()
    primary = json.loads(
        (private / "cohort-preparation.json").read_text(encoding="utf-8")
    )
    assert len(candidates) == primary["candidate_pairs"] == 72
    checks += 1
    selected = []
    parents = set()
    targets = set()
    lookups = set()
    for parent, target, i, j, asserted in candidates:
        if parent in parents or target in targets:
            continue
        key = "datacite-" + hashlib.sha256(target.encode()).hexdigest()[:20]
        assert key in latest, "Missing qualification before the first16 selected pairs"
        receipt = latest[key]
        lookups.add(key)
        assert len(lookups) <= 40
        if receipt["http_status"] == 404:
            continue
        assert receipt["http_status"] == 200
        path = private / receipt["body_file"]
        row = connection.execute(
            "select json_extract_string(json,'$.data.id'),json_extract_string(json,'$.data.attributes.doi'),json_extract_string(json,'$.data.attributes.state'),json_extract_string(json,'$.data.attributes.types.resourceTypeGeneral'),json_extract_string(json,'$.data.attributes.url') from read_json_objects(?)",
            [str(path)],
        ).fetchone()
        native_id, attribute_id, state, resource, url = row
        assert normalize(native_id) == normalize(attribute_id) == target
        checks += 2
        if state != "findable" or resource != "Dataset":
            continue
        parsed = urlsplit(url)
        assert parsed.scheme in ["http", "https"]
        checks += 1
        selected.append(
            dict(
                parent_doi=parent,
                dataset_doi=target,
                crossref_item_index=i,
                relation_index=j,
                asserted_by=asserted,
                state=state,
                resource_type_general=resource,
                registry_landing_url_sanitized=urlunsplit(
                    (parsed.scheme, parsed.hostname or "", parsed.path, "", "")
                ),
                source_key=key,
            )
        )
        parents.add(parent)
        targets.add(target)
        if len(selected) == 16:
            break
    assert len(selected) == len(parents) == len(targets) == 16
    for i, (expected, actual) in enumerate(
        zip(selected, primary["selected"], strict=True)
    ):
        expected["phase"] = "development" if i % 2 == 0 else "final"
        for name, value in expected.items():
            assert actual[name] == value
            checks += 1
    ledger = primary["ledger"]
    keys = {(r["crossref_item_index"], r.get("relation_index")) for r in ledger}
    by_item = Counter(i for i, j, parent, target, idtype, asserted in native)
    assert (
        len(keys)
        == len(ledger)
        == len(native) + sum(i not in by_item for i, doi, kind, year in items)
    )
    connection.execute(
        "create table cohort(parent text unique,target text unique,phase text)"
    )
    connection.executemany(
        "insert into cohort values(?,?,?)",
        [(r["parent_doi"], r["dataset_doi"], r["phase"]) for r in selected],
    )
    assert connection.execute(
        "select phase,count(*) from cohort group by phase order by phase"
    ).fetchall() == [("development", 8), ("final", 8)]
    checks += 2
    for receipt in receipts:
        assert (
            hashlib.sha256((private / receipt["body_file"]).read_bytes()).hexdigest()
            == receipt["sha256"]
        )
        checks += 1
    result = dict(
        status="PASS_INDEPENDENT_DUCKDB_METADATA_AND_SQL",
        duckdb_version=duckdb.__version__,
        conditions_checked=checks,
        source_items=100,
        candidate_pairs=len(candidates),
        metadata_lookups=len(lookups),
        selected_pairs=16,
        phase_counts=dict(development=8, final=8),
        source_files_unchanged=len(receipts),
        landing_observations_made=False,
        data_files_downloaded=False,
        selected=selected,
        source_receipt_sha256=hashlib.sha256(
            (private / "acquisition-receipts.json").read_bytes()
        ).hexdigest(),
    )
    assert not args.output.exists()
    args.output.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(
        json.dumps({key: value for key, value in result.items() if key != "selected"})
    )


if __name__ == "__main__":
    main()
