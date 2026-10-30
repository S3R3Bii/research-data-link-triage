"""Read captured or explicitly authored observations; make no network requests."""

import argparse, json
from pathlib import Path
from rules import compare


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        if args.input.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Observation file exceeds size bound")
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        print(
            json.dumps(
                dict(
                    status="OFFLINE_TRIAGE",
                    **compare(payload["first"], payload.get("second"))
                ),
                allow_nan=False,
            )
        )
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(
            json.dumps(
                dict(
                    status="UNAVAILABLE",
                    reason=str(error),
                    data_file_availability="not_tested",
                )
            )
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
