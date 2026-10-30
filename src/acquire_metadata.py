"""Capture the fixed public metadata once; never probe dataset landing pages."""

import argparse, hashlib, json, time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from identifiers import canonical_doi

ROOT = Path(__file__).resolve().parents[1]
UA = "ResearchLinkReliabilityStudy/0.1 (bounded public metadata qualification)"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    private = args.private_root.resolve()
    assert not private.is_relative_to(ROOT)
    private.mkdir(parents=True, exist_ok=args.resume)
    plan = json.loads((ROOT / "data/source_requests.json").read_text(encoding="utf-8"))
    receipts_path = private / "acquisition-receipts.json"
    receipts = (
        json.loads(receipts_path.read_text(encoding="utf-8"))
        if receipts_path.exists()
        else []
    )
    last = {}
    total = sum(r.get("stored_bytes", 0) for r in receipts)
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    def save():
        receipts_path.write_bytes((json.dumps(receipts, indent=2) + "\n").encode())

    def fetch(key, url, params=None):
        nonlocal total
        target = private / (key + ".json")
        prior = [r for r in receipts if r["key"] == key]
        if prior and prior[-1].get("http_status") in [200, 404]:
            saved = private / prior[-1]["body_file"]
            assert hashlib.sha256(saved.read_bytes()).hexdigest() == prior[-1]["sha256"]
            return prior[-1]["http_status"], json.loads(
                saved.read_text(encoding="utf-8")
            )
        if prior:
            assert (
                args.resume and len(prior) == 1
            ), "At most one targeted retry; preserve repeated source failure"
            assert (
                prior[-1].get("http_status") == 429
            ), "No silent retry of a different source failure"
            assert not prior[-1].get(
                "retry_header_unparsed", False
            ), "Unknown Retry-After remains a source gate"
            assert datetime.now(timezone.utc) >= datetime.fromisoformat(
                prior[-1]["retry_not_before_utc"]
            ), "Rate-limit cooldown is not complete"
        host = urlsplit(url).hostname
        assert (
            host in ["api.crossref.org", "api.datacite.org"]
            and urlsplit(url).scheme == "https"
        )
        if host in last:
            time.sleep(max(0, 2 - (time.monotonic() - last[host])))
        receipt = dict(
            key=key,
            url=url,
            params=params,
            started_utc=datetime.now(timezone.utc).isoformat(),
            attempt=len(prior) + 1,
        )
        try:
            with session.get(
                url, params=params, timeout=(15, 45), stream=True, allow_redirects=False
            ) as response:
                receipt.update(
                    url=response.url,
                    http_status=response.status_code,
                    headers={
                        k: response.headers[k]
                        for k in [
                            "Content-Type",
                            "Content-Length",
                            "Content-Encoding",
                            "Last-Modified",
                            "ETag",
                            "Retry-After",
                            "X-Rate-Limit-Limit",
                            "X-Rate-Limit-Interval",
                            "X-Concurrency-Limit",
                        ]
                        if k in response.headers
                    },
                )
                chunks = []
                size = 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    total += len(chunk)
                    assert (
                        size <= 2 * 1024 * 1024 and total <= 100 * 1024 * 1024
                    ), "Metadata byte budget exceeded"
                    chunks.append(chunk)
                body = b"".join(chunks)
                attempt_path = target if not prior else private / (key + "-retry.json")
                with attempt_path.open("xb") as output:
                    output.write(body)
                receipt.update(
                    stored_bytes=size,
                    sha256=hashlib.sha256(body).hexdigest(),
                    stored_representation="Requests-decoded entity, not compressed wire bytes",
                    completed_utc=datetime.now(timezone.utc).isoformat(),
                    body_file=attempt_path.name,
                )
                if response.status_code == 429:
                    retry = response.headers.get("Retry-After", "")
                    delay = max(60, int(retry)) if retry.isdigit() else 60
                    due = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    if retry and not retry.isdigit():
                        try:
                            native_due = parsedate_to_datetime(retry)
                            assert native_due.tzinfo is not None
                            due = max(due, native_due.astimezone(timezone.utc))
                        except (ValueError, TypeError, AssertionError):
                            receipt["retry_header_unparsed"] = True
                    receipt["retry_not_before_utc"] = due.isoformat()
                    receipt["status"] = "RATE_LIMIT_STOP_NO_AUTOMATIC_RETRY"
                    receipts.append(receipt)
                    save()
                    raise RuntimeError(
                        "429 captured; honor the recorded cooldown and inspect any date-valued Retry-After before one targeted resume"
                    )
                assert response.status_code in [
                    200,
                    404,
                ], f"Unexpected HTTP {response.status_code}; source capture stopped"
                value = json.loads(body)
                receipt["status"] = "CAPTURED_VERIFIED_HTTPS"
                receipts.append(receipt)
                save()
                print(
                    json.dumps(
                        dict(
                            key=key,
                            http_status=response.status_code,
                            stored_bytes=size,
                            status=receipt["status"],
                        )
                    ),
                    flush=True,
                )
                return response.status_code, value
        except Exception as error:
            if not receipts or receipts[-1] is not receipt:
                receipt.update(
                    status="FAILED_RETAINED",
                    error_type=type(error).__name__,
                    error=str(error),
                    completed_utc=datetime.now(timezone.utc).isoformat(),
                )
                receipts.append(receipt)
                save()
            raise
        finally:
            last[host] = time.monotonic()

    status, body = fetch(
        "crossref-cohort", plan["crossref"]["url"], plan["crossref"]["params"]
    )
    assert status == 200
    items = body["message"]["items"]
    assert len(items) == 100 and body["message"]["total-results"] >= 100
    candidate_pairs = []
    ledger = []
    for i, item in enumerate(items):
        parent = canonical_doi(item["DOI"])
        assert item["type"] == "journal-article"
        relations = item.get("relation", {}).get("is-supplemented-by", [])
        if not relations:
            ledger.append(
                dict(
                    crossref_item_index=i,
                    parent_doi=parent,
                    status="no_actual_is_supplemented_by_entry",
                )
            )
        for j, relation in enumerate(relations):
            entry = dict(
                crossref_item_index=i,
                relation_index=j,
                parent_doi=parent,
                relation_type="is-supplemented-by",
                asserted_by=relation.get("asserted-by"),
                native_id_sha256=hashlib.sha256(
                    str(relation.get("id")).encode()
                ).hexdigest(),
            )
            if relation.get("id-type", "").lower() != "doi" or relation.get(
                "asserted-by"
            ) not in ["subject", "object"]:
                entry["status"] = "unsupported_relation_type_or_provenance"
                ledger.append(entry)
                continue
            try:
                target = canonical_doi(relation["id"])
            except ValueError:
                entry["status"] = "unsupported_or_ambiguous_identifier"
                ledger.append(entry)
                continue
            entry["dataset_doi"] = target
            if target.split("/")[0] not in plan["eligible_prefixes"]:
                entry["status"] = "outside_fixed_repository_prefixes"
                ledger.append(entry)
                continue
            entry["status"] = "candidate_before_datacite"
            candidate_pairs.append(entry)
    candidate_pairs.sort(
        key=lambda row: (
            row["parent_doi"],
            row["dataset_doi"],
            row["crossref_item_index"],
            row["relation_index"],
        )
    )
    metadata = {}
    selected = []
    parents = set()
    targets = set()
    lookups = 0
    for index, pair in enumerate(candidate_pairs):
        target = pair["dataset_doi"]
        entry = dict(pair)
        if pair["parent_doi"] in parents or target in targets:
            entry["status"] = "duplicate_selected_parent_or_target"
            ledger.append(entry)
            continue
        if target not in metadata:
            if lookups >= plan["metadata_lookup_cap"]:
                entry["status"] = "metadata_lookup_cap_before_qualification"
                ledger.append(entry)
                break
            key = "datacite-" + hashlib.sha256(target.encode()).hexdigest()[:20]
            code, record = fetch(key, plan["datacite_base"] + quote(target, safe="/"))
            lookups += 1
            if code == 404:
                metadata[target] = dict(
                    status="public_findable_record_not_found", source_key=key
                )
            else:
                native = record["data"]
                attributes = native["attributes"]
                assert (
                    canonical_doi(native["id"])
                    == canonical_doi(attributes["doi"])
                    == target
                )
                state = attributes.get("state")
                resource = attributes.get("types", {}).get("resourceTypeGeneral")
                if state != "findable" or resource != "Dataset":
                    metadata[target] = dict(
                        status="not_findable_dataset_type",
                        state=state,
                        resource_type_general=resource,
                        source_key=key,
                    )
                else:
                    url = urlsplit(attributes["url"])
                    assert url.scheme in ["http", "https"]
                    metadata[target] = dict(
                        status="qualified_findable_dataset",
                        state=state,
                        resource_type_general=resource,
                        source_key=key,
                        registry_landing_url_sanitized=urlunsplit(
                            (url.scheme, url.hostname or "", url.path, "", "")
                        ),
                    )
        entry.update(metadata[target])
        ledger.append(entry)
        if entry["status"] == "qualified_findable_dataset":
            selected.append(entry)
            parents.add(pair["parent_doi"])
            targets.add(target)
        if len(selected) == plan["required_real_dataset_pairs"]:
            for rest in candidate_pairs[index + 1 :]:
                ledger.append(
                    dict(rest, status="not_examined_after_fixed_cohort_complete")
                )
            break
    result = dict(
        status=(
            "PRIMARY_METADATA_SELECTION_PENDING_INDEPENDENT_CHECK"
            if len(selected) == 16
            else "SOURCE_HOLD_FIXED_COHORT_INCOMPLETE"
        ),
        protocol_commit="1b1165c75e186ad712ba05f57a65bd05977d5023",
        source_items=len(items),
        candidate_pairs=len(candidate_pairs),
        metadata_lookups=lookups,
        selected_pairs=len(selected),
        selected=selected,
        ledger=ledger,
        landing_observations_made=False,
        data_files_downloaded=False,
        completed_utc=datetime.now(timezone.utc).isoformat(),
    )
    for i, pair in enumerate(selected):
        pair["phase"] = "development" if i % 2 == 0 else "final"
    (private / "cohort-preparation.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    assert (
        len(selected) == 16
    ), "Fixed cohort incomplete; do not expand this query or choose by landing outcomes"
    print(
        json.dumps(
            {
                key: result[key]
                for key in [
                    "status",
                    "source_items",
                    "candidate_pairs",
                    "metadata_lookups",
                    "selected_pairs",
                    "landing_observations_made",
                    "data_files_downloaded",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
