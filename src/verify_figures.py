"""Reconstruct response/action/timing values and actual SVG coordinates."""

import hashlib, json, re
from fractions import Fraction as F
from pathlib import Path
from xml.etree import ElementTree as E

ROOT = Path(__file__).resolve().parents[1]
SVG = "{http://www.w3.org/2000/svg}"


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def vertices(node):
    path = node.find(".//" + SVG + "path")
    assert path is not None
    values = [
        float(v)
        for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", path.attrib["d"])
    ]
    assert len(values) % 2 == 0
    return list(zip(values[::2], values[1::2]))


from datetime import datetime


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def decisions(first, second):
    code = first["terminal_http_status"]
    naive = (
        "broken_alert"
        if first["transport_error_type"]
        or (first["request_made"] and code is not None and not 200 <= code < 300)
        else ("no_broken_alert" if first["request_made"] else "unknown_no_alert")
    )
    spaced = (
        stamp(second["started_utc"]) - stamp(first["completed_utc"])
    ).total_seconds() >= 1200
    review = (
        spaced
        and first["terminal_http_status"] in [404, 410]
        and second["terminal_http_status"] in [404, 410]
        and first["censor_reason"] is None
        and second["censor_reason"] is None
    )
    conservative = "review_doi_candidate" if review else "unknown"
    return dict(naive_action=naive, conservative_action=conservative)


def main():
    data = {
        phase: [
            read("data/observations_" + phase + "_" + str(n) + ".json")["observations"]
            for n in [1, 2]
        ]
        for phase in ["development", "final"]
    }
    maps = read("figures/value_map.json")
    assert len(maps) == 3
    values = coordinates = hashes = 0
    for figure in maps:
        tree = E.parse(ROOT / "figures" / (figure["figure"] + ".svg"))
        ids = {n.get("id"): n for n in tree.iter() if n.get("id")}
        areas = []
        for i, axis in enumerate(figure["axes"]):
            points = vertices(ids["data-area-" + str(i)])
            areas.append(
                (
                    min(x for x, y in points),
                    max(x for x, y in points),
                    min(y for x, y in points),
                    max(y for x, y in points),
                )
            )
        for item in figure["items"]:
            axis = figure["axes"][item["axis"]]
            xmin, xmax = axis["xlim"]
            ymin, ymax = axis["ylim"]
            kind = item["kind"]
            if kind == "status":
                rows = data[item["phase"]][item["number"] - 1]
                value = sum(
                    r["role"] == item["role"]
                    and r["terminal_http_status"] == item["status"]
                    for r in rows
                )
                for row in rows:
                    if row["role"] == "real_dataset":
                        assert (
                            row["terminal_http_status"] == 202
                            and row["chain"][-1]["stored_bytes"] == 0
                        )
                j = [(p, n) for p in ["development", "final"] for n in [1, 2]].index(
                    (item["phase"], item["number"])
                )
                k = ["real_dataset", "authored_control"].index(item["role"])
                x = j + [-0.18, 0.18][k]
                assert (
                    axis == dict(xlim=[-0.6, 3.6], ylim=[0, 11])
                    and item["x"] == x
                    and item["width"] == 0.32
                    and item["display"] == str(value)
                )
                expected = [
                    (x - 0.16, 0),
                    (x + 0.16, 0),
                    (x + 0.16, value),
                    (x - 0.16, value),
                ]
                values += 1
            elif kind == "action":
                derived = [
                    dict(decisions(a, b), role=a["role"])
                    for a, b in zip(*data[item["phase"]])
                ]
                field = item["field"]
                positive = (
                    "broken_alert"
                    if field == "naive_action"
                    else "review_doi_candidate"
                )
                category = item["category"]
                value = sum(
                    (
                        (r["role"] == "real_dataset" and r[field] == positive)
                        if category == 0
                        else (
                            (r["role"] == "authored_control" and r[field] == positive)
                            if category == 1
                            else (
                                r["role"] == "real_dataset"
                                and r[field]
                                in ["unknown", "unknown_no_alert", "await_repeat"]
                            )
                        )
                    )
                    for r in derived
                )
                k = ["naive_action", "conservative_action"].index(field)
                x = category + [-0.18, 0.18][k]
                assert (
                    axis == dict(xlim=[-0.6, 2.6], ylim=[0, 10])
                    and item["x"] == x
                    and item["width"] == 0.32
                    and item["display"] == str(value)
                )
                expected = [
                    (x - 0.16, 0),
                    (x + 0.16, 0),
                    (x + 0.16, value),
                    (x - 0.16, value),
                ]
                values += 1
            else:
                assert kind == "timing"
                phase = item["phase"]
                first, second = data[phase]
                j = next(
                    i for i, r in enumerate(first) if r["item_id"] == item["item_id"]
                )
                ref = next(
                    r
                    for r in read("data/references_" + phase + ".json")["records"]
                    if r["item_id"] == item["item_id"]
                )
                x = (
                    stamp(ref["reference_utc"]) - stamp(first[j]["completed_utc"])
                ).total_seconds() / 60
                value = (
                    stamp(second[j]["started_utc"]) - stamp(first[j]["completed_utc"])
                ).total_seconds() / 60
                y = 10 - j
                assert (
                    axis == dict(xlim=[0, 22.5], ylim=[-0.6, 10.6])
                    and item["x"] == x
                    and item["y"] == y
                )
                expected = [(x, y), (value, y)]
                values += 2
            assert item["value"] == value
            left, right, top, bottom = areas[item["axis"]]
            projected = [
                (
                    left + (x - xmin) / (xmax - xmin) * (right - left),
                    bottom - (y - ymin) / (ymax - ymin) * (bottom - top),
                )
                for x, y in expected
            ]
            actual = vertices(ids[item["id"]])
            assert len(actual) == len(projected), (item["id"], actual, projected)
            for (x, y), (a, b) in zip(actual, projected):
                assert abs(x - a) <= 3e-5 and abs(y - b) <= 3e-5, (
                    item["id"],
                    (x, y),
                    (a, b),
                )
                coordinates += 2
        for name, digest in figure["files"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            hashes += 1
        assert (ROOT / "paper/images" / (figure["figure"] + ".pdf")).read_bytes() == (
            ROOT / "figures" / (figure["figure"] + ".pdf")
        ).read_bytes()
    result = dict(
        status="PASS_SOURCE_VALUES_AND_ACTUAL_SVG_GEOMETRY",
        figure_values=values,
        SVG_coordinates=coordinates,
        format_hashes=hashes,
        figures=3,
        scope="Finite values/geometry/hashes; source data-file access and human reference validation remain untested",
    )
    (ROOT / "figures/verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
