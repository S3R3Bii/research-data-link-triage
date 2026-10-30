"""Fixed response-triage comparators; no inference about dataset-file access."""

from datetime import datetime, timezone
from identifiers import canonical_doi

REPEAT_MINIMUM_SECONDS = 1200
VALID_STATUS_CENSORS = [None, "non_html_response_not_consumed", "rate_limit_observed"]


def utc(value):
    if not isinstance(value, str):
        raise ValueError("Missing UTC observation time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone is required")
    return parsed.astimezone(timezone.utc)


def validate(observation):
    if not isinstance(observation, dict):
        raise ValueError("Observation must be an object")
    canonical_doi(observation["dataset_doi"])
    if observation["role"] not in [
        "real_dataset",
        "authored_control",
        "authored_example",
    ]:
        raise ValueError("Unsupported observation role")
    if type(observation["request_made"]) is not bool:
        raise ValueError("Request state must be boolean")
    code = observation["terminal_http_status"]
    if code is not None and (type(code) is not int or not 100 <= code <= 599):
        raise ValueError("Invalid HTTP status")
    if utc(observation["completed_utc"]) < utc(observation["started_utc"]):
        raise ValueError("Reversed observation interval")
    if (
        observation["data_file_availability"] != "not_tested"
        or observation["data_file_body_downloaded"] is not False
    ):
        raise ValueError("Dataset-file access is outside this exercise")
    if (
        observation["role"] != "authored_example"
        and observation["request_made"]
        and not observation.get("chain")
    ):
        raise ValueError("Captured request chain is missing")
    return observation


def response_class(observation):
    validate(observation)
    if observation.get("transport_error_type"):
        return "transport_failure"
    if observation.get("censor_reason") not in VALID_STATUS_CENSORS:
        return "censored_or_not_observed"
    if not observation["request_made"]:
        return "not_observed"
    code = observation["terminal_http_status"]
    if code is None:
        return "incomplete_without_status"
    if code == 202:
        return "accepted_not_completed"
    if code == 204:
        return "no_content_response"
    if 200 <= code <= 299:
        return "http_response_observed"
    if code in [404, 410]:
        return "not_found_response"
    if code in [401, 403]:
        return "access_restricted"
    if code == 429:
        return "rate_limited"
    if 500 <= code <= 599:
        return "server_failure"
    if 300 <= code <= 399:
        return "redirect_incomplete"
    return "other_response"


def naive(first):
    validate(first)
    code = first["terminal_http_status"]
    if first.get("transport_error_type"):
        return "broken_alert"
    if not first["request_made"]:
        return "unknown_no_alert"
    if code is not None and not 200 <= code <= 299:
        return "broken_alert"
    return "no_broken_alert"


def compare(first, second=None):
    first_class = response_class(first)
    second_class = response_class(second) if second is not None else None
    gap = None
    if second is not None:
        if (
            first["item_id"] != second["item_id"]
            or canonical_doi(first["dataset_doi"])
            != canonical_doi(second["dataset_doi"])
            or first["role"] != second["role"]
        ):
            raise ValueError("Observation identities differ")
        gap = (utc(second["started_utc"]) - utc(first["completed_utc"])).total_seconds()
        if gap < 0:
            raise ValueError("Observation chronology is reversed")
    action = "unknown"
    reason = first_class if second is None else second_class
    if second is not None and gap < REPEAT_MINIMUM_SECONDS:
        reason = "repeat_too_early"
    elif first_class == "not_found_response" and second_class == "not_found_response":
        action = "review_doi_candidate"
        reason = "two_spaced_not_found_responses"
    elif first_class == "not_found_response" and second is None:
        action = "await_repeat"
        reason = "one_not_found_response"
    elif second_class == "http_response_observed":
        action = "http_response_observed"
        reason = (
            "response_changed_from_not_found"
            if first_class == "not_found_response"
            else "later_http_response_only"
        )
    elif second is None and first_class == "http_response_observed":
        action = "http_response_observed"
        reason = "first_http_response_only"
    elif first_class == "accepted_not_completed" and second_class in [
        None,
        "accepted_not_completed",
    ]:
        reason = "accepted_processing_not_completed"
    return dict(
        item_id=first["item_id"],
        dataset_doi=canonical_doi(first["dataset_doi"]),
        role=first["role"],
        naive_action=naive(first),
        conservative_action=action,
        reason=reason,
        first_response_class=first_class,
        second_response_class=second_class,
        repeat_gap_seconds=gap,
        data_file_availability="not_tested",
        scope="DOI response triage; dataset files and scientific claims have not been verified",
    )
