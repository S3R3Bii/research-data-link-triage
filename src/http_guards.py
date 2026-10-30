"""Bound public landing checks; unknown access is never proof of missing data."""

import ipaddress
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlsplit, urlunsplit

ALLOWED = [
    "doi.org",
    "dx.doi.org",
    "api.crossref.org",
    "api.datacite.org",
    "zenodo.org",
    "datadryad.org",
    "figshare.com",
]


def check_url(value):
    try:
        url = urlsplit(value)
        if url.scheme != "https":
            return "non_https_not_probed"
        if url.username or url.password:
            return "credentials_not_probed"
        if url.port not in [None, 443]:
            return "nonstandard_port_not_probed"
        host = (url.hostname or "").lower()
        try:
            ipaddress.ip_address(host)
            return "literal_ip_not_probed"
        except ValueError:
            pass
        if not any(
            host == base
            or (
                base in ["zenodo.org", "datadryad.org", "figshare.com"]
                and host.endswith("." + base)
            )
            for base in ALLOWED
        ):
            return "off_allowlist_not_probed"
        if host.startswith(("ndownloader.", "download.")) or re.search(
            r"/(?:files?|downloads?|ndownloader)(?:/|$)", url.path, re.I
        ):
            return "file_download_not_probed"
        if any(
            re.search(
                r"token|secret|password|signature|api.?key|credential|authorization",
                key,
                re.I,
            )
            for key, value in parse_qsl(url.query, keep_blank_values=True)
        ):
            return "sensitive_query_not_probed"
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            return "control_character_not_probed"
        return None
    except (ValueError, TypeError):
        return "malformed_url_not_probed"


def public_url(value):
    if check_url(value):
        return "redacted_disallowed_target"
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))


def retry_deadline(raw, observed):
    moment = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    floor = moment + timedelta(seconds=60)
    if not raw:
        return floor.isoformat()
    if raw.isdigit():
        return max(floor, moment + timedelta(seconds=int(raw))).isoformat()
    parsed = parsedate_to_datetime(raw)
    if parsed.tzinfo is None:
        raise ValueError("Retry-After date lacks timezone")
    return max(floor, parsed.astimezone(timezone.utc)).isoformat()


def repeat_due(first_observations, now):
    if not first_observations:
        raise ValueError("First observations are missing")
    latest = max(
        datetime.fromisoformat(row["completed_utc"].replace("Z", "+00:00"))
        for row in first_observations
    )
    due = latest + timedelta(seconds=1200)
    if now < due:
        raise ValueError("Repeat not due until " + due.isoformat())
    return due
