"""Require the original committed development dependencies before final probes."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)

    path = "data/dependency_freeze.json"
    raw = (ROOT / path).read_bytes()
    assert raw == git("show", "HEAD:" + path), "Manifest must be committed"
    freeze = git("log", "--format=%H", "--", path).decode().splitlines()[-1]
    assert raw == git("show", freeze + ":" + path), "Do not replace the original freeze"
    manifest = json.loads(raw)
    development = manifest["development_commit"]
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", development, freeze],
        cwd=ROOT,
        check=True,
    )
    names = git("ls-tree", "-r", "--name-only", freeze).decode().splitlines()
    assert not any(
        name.startswith(
            ("data/observations_final", "data/references_final", "results/final/")
        )
        for name in names
    ), "Final outcomes preceded freeze"
    for item in manifest["files"]:
        data = (ROOT / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"], item["path"]
        assert data == git("show", freeze + ":" + item["path"]), item["path"]
    print(
        json.dumps(
            dict(
                status="PASS_COMMITTED_BEFORE_FINAL",
                files=len(manifest["files"]),
                development_commit=development,
                freeze_commit=freeze,
                final_observations_and_references_absent_at_freeze=True,
            )
        )
    )


if __name__ == "__main__":
    main()
