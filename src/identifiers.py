"""Conservative DOI normalization for this bounded ASCII metadata cohort."""

import re
from urllib.parse import urlsplit


def canonical_doi(value):
    if not isinstance(value, str):
        raise ValueError("Identifier is not a string")
    token = value.strip()
    if token.lower().startswith("https://"):
        url = urlsplit(token)
        if (
            url.hostname not in ["doi.org", "dx.doi.org"]
            or url.username
            or url.password
            or url.port not in [None, 443]
            or url.query
            or url.fragment
        ):
            raise ValueError("Ambiguous DOI wrapper")
        token = url.path.lstrip("/")
    if (
        not token.isascii()
        or len(token) > 240
        or any(c in token for c in ["?", "#", "%", "@"])
    ):
        raise ValueError("Unsupported or ambiguous identifier representation")
    if re.fullmatch(r"10\.\d{4,9}/[^\s\x00-\x20\x7f]+", token) is None:
        raise ValueError("Unsupported DOI syntax")
    return token.lower()
