"""Take one bounded landing observation; never download source dataset files."""

import argparse, csv, hashlib, ipaddress, json, socket, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
import requests
from http_guards import check_url, public_url, repeat_due, retry_deadline

ROOT = Path(__file__).resolve().parents[1]
BODY_CAP = 256 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode())


def observe(item, folder, session, last, blocked):
    url = "https://doi.org/" + quote(item["dataset_doi"], safe="/")
    started = now()
    steps = []
    reason = None
    terminal = None
    transport = None
    visited = set()
    for index in range(6):
        reason = check_url(url)
        if reason:
            break
        if url in visited:
            reason = "redirect_loop"
            break
        visited.add(url)
        host = urlsplit(url).hostname
        if host in blocked:
            reason = "not_requested_host_rate_limit"
            break
        step = dict(url=url, started_utc=now())
        body = b""
        pieces = []
        try:
            addresses = {
                row[4][0]
                for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            }
            if not addresses or any(
                not ipaddress.ip_address(address).is_global for address in addresses
            ):
                reason = "private_or_nonpublic_dns_target_not_probed"
                break
            if host in last:
                time.sleep(max(0, 2 - (time.monotonic() - last[host])))
            session.cookies.clear()
            step["request_started_utc"] = now()
            step["http_request_attempted"] = True
            with session.get(
                url, timeout=(15, 30), allow_redirects=False, stream=True
            ) as response:
                code = response.status_code
                terminal = code
                headers = {
                    key: response.headers[key]
                    for key in [
                        "Content-Type",
                        "Content-Length",
                        "Content-Encoding",
                        "Location",
                        "Retry-After",
                        "Date",
                    ]
                    if key in response.headers
                }
                step.update(
                    http_status=code,
                    headers=headers,
                    body_truncated=False,
                    body_consumed=False,
                )
                redirect = code in [301, 302, 303, 307, 308]
                content = (
                    response.headers.get("Content-Type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                if not redirect and content in ["text/html", "application/xhtml+xml"]:
                    pieces = []
                    size = 0
                    for chunk in response.iter_content(32768):
                        take = min(len(chunk), BODY_CAP - size)
                        pieces.append(chunk[:take])
                        size += take
                        if take < len(chunk):
                            step["body_truncated"] = True
                            break
                    body = b"".join(pieces)
                    step["body_consumed"] = True
                elif not redirect:
                    reason = "non_html_response_not_consumed"
                if code == 429:
                    try:
                        due = retry_deadline(
                            response.headers.get("Retry-After", ""), now()
                        )
                        step["retry_not_before_utc"] = due
                        blocked[host] = due
                    except (ValueError, TypeError, OverflowError):
                        step["retry_not_before_utc"] = None
                        step["retry_header_unparsed"] = True
                        blocked[host] = None
                    reason = "rate_limit_observed"
                step["completed_utc"] = now()
                if redirect:
                    location = response.headers.get("Location")
                    if not location:
                        reason = "redirect_without_location"
                    elif index == 5:
                        reason = "redirect_limit"
                    else:
                        step["next_url"] = urljoin(url, location)
                        url = step["next_url"]
        except (requests.RequestException, OSError) as error:
            body = b"".join(pieces)
            transport = type(error).__name__
            reason = "transport_failure"
            step.update(
                transport_error_type=transport,
                private_error=str(error),
                completed_utc=now(),
            )
        finally:
            last[host] = time.monotonic()
            if "http_status" in step or "transport_error_type" in step:
                body_path = folder / ("request-" + str(index) + ".body")
                body_path.write_bytes(body)
                step.update(
                    stored_bytes=len(body),
                    body_sha256=hashlib.sha256(body).hexdigest(),
                    body_file=body_path.name,
                )
                steps.append(step)
        if transport or reason:
            break
        if steps[-1].get("next_url") is None:
            break
    completed = now()
    safe_steps = []
    for step in steps:
        headers = step.get("headers", {})
        safe_steps.append(
            dict(
                url=public_url(step["url"]),
                http_status=step.get("http_status"),
                started_utc=step["started_utc"],
                request_started_utc=step.get("request_started_utc"),
                completed_utc=step.get("completed_utc", completed),
                content_type=headers.get("Content-Type"),
                redirect_target=(
                    public_url(step["next_url"]) if step.get("next_url") else None
                ),
                retry_after=headers.get("Retry-After"),
                retry_not_before_utc=step.get("retry_not_before_utc"),
                retry_header_unparsed=step.get("retry_header_unparsed", False),
                stored_bytes=step.get("stored_bytes", 0),
                body_sha256=step.get("body_sha256"),
                body_consumed=step.get("body_consumed", False),
                body_truncated=step.get("body_truncated", False),
                transport_error_type=step.get("transport_error_type"),
            )
        )
    record = dict(
        item_id=item["item_id"],
        dataset_doi=item["dataset_doi"],
        role=item["role"],
        started_utc=started,
        completed_utc=completed,
        request_made=any(step.get("http_request_attempted") for step in steps),
        terminal_http_status=terminal,
        final_url=public_url(url),
        censor_reason=reason,
        transport_error_type=transport,
        chain=safe_steps,
        data_file_availability="not_tested",
        data_file_body_downloaded=False,
    )
    raw = dict(public_record=record, raw_steps=steps)
    dump(folder / "transcript.json", raw)
    record["private_transcript_sha256"] = hashlib.sha256(
        (folder / "transcript.json").read_bytes()
    ).hexdigest()
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["development", "final"], required=True)
    parser.add_argument("--number", type=int, choices=[1, 2], required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    args = parser.parse_args()
    private = args.private_root.resolve()
    assert not private.is_relative_to(ROOT)
    if args.phase == "final":
        proof = ROOT / "src/freeze_proof.py"
        assert (
            proof.exists()
        ), "Final group is protected until the committed dependency proof exists"
        subprocess.run([sys.executable, str(proof)], cwd=ROOT, check=True)
    public_path = (
        ROOT
        / "data"
        / ("observations_" + args.phase + "_" + str(args.number) + ".json")
    )
    assert (
        not public_path.exists()
    ), "Keep prior snapshots; do not rerun a completed observation"
    with (ROOT / "data/cohort.csv").open(newline="", encoding="utf-8") as source:
        cohort = [row for row in csv.DictReader(source) if row["phase"] == args.phase]
    assert len(cohort) == 11
    cohort_sha = hashlib.sha256((ROOT / "data/cohort.csv").read_bytes()).hexdigest()
    prior = {}
    if args.number == 2:
        first_path = ROOT / "data" / ("observations_" + args.phase + "_1.json")
        first = json.loads(first_path.read_text(encoding="utf-8"))
        assert first["cohort_sha256"] == cohort_sha
        repeat_due(first["observations"], datetime.now(timezone.utc))
        prior = {r["item_id"]: r for r in first["observations"]}
    folder = private / args.phase / ("probe" + str(args.number))
    folder.mkdir(parents=True, exist_ok=False)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "ResearchLinkReliabilityStudy/0.1 (bounded public metadata qualification)"
        }
    )
    last = {}
    blocked = {}
    records = []
    for item in cohort:
        output = folder / item["item_id"]
        output.mkdir()
        previous = prior.get(item["item_id"])
        skip = None
        if previous and previous["terminal_http_status"] in [401, 403]:
            skip = "not_requested_after_denial"
        if previous and previous["terminal_http_status"] == 429:
            last_step = previous["chain"][-1]
            deadline = last_step.get("retry_not_before_utc")
            if last_step.get("retry_header_unparsed") or not deadline:
                skip = "not_requested_unknown_retry_after"
            elif datetime.now(timezone.utc) < datetime.fromisoformat(deadline):
                skip = "not_requested_before_retry_after"
        if skip:
            record = dict(
                item_id=item["item_id"],
                dataset_doi=item["dataset_doi"],
                role=item["role"],
                started_utc=now(),
                completed_utc=now(),
                request_made=False,
                terminal_http_status=None,
                final_url="not_requested",
                censor_reason=skip,
                transport_error_type=None,
                chain=[],
                data_file_availability="not_tested",
                data_file_body_downloaded=False,
            )
            dump(output / "transcript.json", dict(public_record=record, raw_steps=[]))
            record["private_transcript_sha256"] = hashlib.sha256(
                (output / "transcript.json").read_bytes()
            ).hexdigest()
        else:
            record = observe(item, output, session, last, blocked)
        records.append(record)
        dump(folder / "partial-observations.json", records)
        print(
            json.dumps(
                {
                    key: record[key]
                    for key in [
                        "item_id",
                        "request_made",
                        "terminal_http_status",
                        "censor_reason",
                        "completed_utc",
                    ]
                }
            ),
            flush=True,
        )
    assert len(records) == 11
    result = dict(
        phase=args.phase,
        number=args.number,
        status="OBSERVATIONS_CAPTURED_PENDING_INDEPENDENT_CHECK",
        cohort_sha256=cohort_sha,
        observations=records,
        earliest_repeat_utc=(
            (
                max(datetime.fromisoformat(r["completed_utc"]) for r in records)
                + timedelta(seconds=1200)
            ).isoformat()
            if args.number == 1
            else None
        ),
        data_file_availability="not_tested",
        data_file_bodies_downloaded=False,
    )
    if args.number == 2:
        result["first_snapshot_sha256"] = hashlib.sha256(
            first_path.read_bytes()
        ).hexdigest()
    dump(public_path, result)
    dump(folder / "snapshot-public-copy.json", result)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "observations"}
        )
    )


if __name__ == "__main__":
    main()
