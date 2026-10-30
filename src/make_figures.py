"""Show captured response states, bounded comparisons and actual observation gaps."""

import hashlib, json, shutil
from fractions import Fraction as F
from pathlib import Path
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
COLORS = ["#235d72", "#b65f27", "#667782"]
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "svg.fonttype": "none",
        "path.simplify": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "text.color": "#233b49",
        "axes.labelcolor": "#233b49",
        "axes.edgecolor": "#a5b2b7",
        "savefig.facecolor": "white",
    }
)


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def finish(fig, axes, name, items):
    for index, ax in enumerate(axes):
        ax.patch.set_gid("data-area-" + str(index))
    for item in items:
        item.pop("artist").set_gid(item["id"])
    files = []
    for ext in ["pdf", "svg", "png"]:
        path = ROOT / "figures" / (name + "." + ext)
        fig.savefig(
            path,
            dpi=155 if ext == "png" else 100,
            metadata={"Date": None} if ext == "svg" else None,
        )
        if ext == "svg":
            path.write_bytes(
                (
                    "\n".join(
                        line.rstrip()
                        for line in path.read_text(encoding="utf-8").splitlines()
                    )
                    + "\n"
                ).encode()
            )
        files.append(path)
    shutil.copyfile(
        ROOT / "figures" / (name + ".pdf"), ROOT / "paper/images" / (name + ".pdf")
    )
    result = dict(
        figure=name,
        axes=[dict(xlim=list(ax.get_xlim()), ylim=list(ax.get_ylim())) for ax in axes],
        items=items,
        files={
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files
        },
    )
    plt.close(fig)
    return result


def main():
    (ROOT / "paper/images").mkdir(parents=True, exist_ok=True)
    maps = []
    fig, ax = plt.subplots(figsize=(9.2, 4.7))
    fig.subplots_adjust(left=0.10, right=0.97, bottom=0.22, top=0.73)
    items = []
    snapshots = [
        (phase, number) for phase in ["development", "final"] for number in [1, 2]
    ]
    for j, (phase, number) in enumerate(snapshots):
        rows = read("data/observations_" + phase + "_" + str(number) + ".json")[
            "observations"
        ]
        for k, (role, status) in enumerate(
            [("real_dataset", 202), ("authored_control", 404)]
        ):
            value = sum(
                r["role"] == role and r["terminal_http_status"] == status for r in rows
            )
            x = j + [-0.18, 0.18][k]
            bar = ax.bar(
                x,
                value,
                width=0.32,
                color=COLORS[k],
                label=(
                    ("Real DOI: empty 202" if k == 0 else "Authored control: 404")
                    if j == 0
                    else None
                ),
            )[0]
            ax.text(x, value + 0.2, str(value), ha="center")
            items.append(
                dict(
                    artist=bar,
                    id="status-" + phase + "-" + str(number) + "-" + str(k),
                    axis=0,
                    kind="status",
                    phase=phase,
                    number=number,
                    role=role,
                    status=status,
                    x=x,
                    width=0.32,
                    value=value,
                    display=str(value),
                )
            )
    ax.set(
        xticks=range(4),
        xticklabels=["Development 1", "Development 2", "Final 1", "Final 2"],
        xlim=(-0.6, 3.6),
        ylim=(0, 11),
        ylabel="Items in each captured response category",
        xlabel="Eleven fixed items per snapshot; repeated items are not independent observations",
    )
    ax.legend(frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.5, 1.13))
    ax.yaxis.grid(alpha=0.15)
    ax.set_axisbelow(True)
    fig.suptitle(
        "The request path returns accepted-but-incomplete responses",
        x=0.10,
        ha="left",
        fontweight="bold",
    )
    fig.text(
        0.10,
        0.85,
        "Every real DOI ends in an empty HTTP 202 response; all authored controls return 404.",
        fontsize=9,
    )
    fig.text(
        0.10,
        0.04,
        "All sixteen real source datasets are Karger Figshare. No source file is explicitly opened or tested.",
        fontsize=8,
    )
    maps.append(finish(fig, [ax], "captured_responses", items))
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.8))
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.24, top=0.70, wspace=0.18)
    items = []
    for i, phase in enumerate(["development", "final"]):
        rows = read("results/" + phase + "/actions.json")
        ax = axes[i]
        for k, (field, positive) in enumerate(
            [
                ("naive_action", "broken_alert"),
                ("conservative_action", "review_doi_candidate"),
            ]
        ):
            values = [
                sum(r["role"] == "real_dataset" and r[field] == positive for r in rows),
                sum(
                    r["role"] == "authored_control" and r[field] == positive
                    for r in rows
                ),
                sum(
                    r["role"] == "real_dataset"
                    and r[field] in ["unknown", "unknown_no_alert", "await_repeat"]
                    for r in rows
                ),
            ]
            for j, value in enumerate(values):
                x = j + [-0.18, 0.18][k]
                bar = ax.bar(
                    x,
                    value,
                    width=0.32,
                    color=COLORS[k],
                    label=(
                        ("Naive first request" if k == 0 else "Conservative repeat")
                        if j == 0
                        else None
                    ),
                )[0]
                ax.text(x, value + 0.2, str(value), ha="center", fontsize=9)
                items.append(
                    dict(
                        artist=bar,
                        id="action-" + phase + "-" + str(k) + "-" + str(j),
                        axis=i,
                        kind="action",
                        phase=phase,
                        field=field,
                        positive=positive,
                        category=j,
                        x=x,
                        width=0.32,
                        value=value,
                        display=str(value),
                    )
                )
        ax.set(
            xticks=range(3),
            xticklabels=["Real:\nalert", "Control:\nalert", "Real:\nunknown"],
            xlim=(-0.6, 2.6),
            ylim=(0, 10),
            title=phase.capitalize(),
            ylabel="Items in each named category" if i == 0 else "",
        )
        ax.yaxis.grid(alpha=0.15)
        ax.set_axisbelow(True)
    axes[0].legend(
        frameon=False,
        ncol=2,
        loc="upper center",
        bbox_to_anchor=(1.08, 1.29),
        fontsize=9,
    )
    fig.suptitle(
        "This cohort shows no apparent false-alert reduction",
        x=0.08,
        ha="left",
        fontweight="bold",
    )
    fig.text(
        0.08,
        0.855,
        "Eight available real browser references and three authored not-found controls in each phase.",
        fontsize=9,
    )
    fig.text(
        0.08,
        0.04,
        "Alert means “broken” for naive, “DOI review candidate” for conservative. Unknown is not verified file access.",
        fontsize=8,
    )
    maps.append(finish(fig, list(axes), "conditional_comparison", items))
    fig, axes = plt.subplots(1, 2, figsize=(10, 5.9))
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.18, top=0.77, wspace=0.23)
    items = []
    for i, phase in enumerate(["development", "final"]):
        refs = read("data/references_" + phase + ".json")["records"]
        actions = read("results/" + phase + "/actions.json")
        ax = axes[i]
        for j, (ref, row) in enumerate(zip(refs, actions)):
            x = ref["probe_to_reference_seconds"] / 60
            repeat = row["repeat_gap_seconds"] / 60
            y = 10 - j
            line = ax.plot(
                [x, repeat],
                [y, y],
                marker="o",
                markersize=3,
                color=COLORS[0] if row["role"] == "real_dataset" else COLORS[1],
                lw=1,
            )[0]
            items.append(
                dict(
                    artist=line,
                    id="timing-" + phase + "-" + row["item_id"],
                    axis=i,
                    kind="timing",
                    phase=phase,
                    item_id=row["item_id"],
                    x=x,
                    value=repeat,
                    y=y,
                )
            )
        ax.set(
            yticks=range(10, -1, -1),
            yticklabels=[r["item_id"] for r in actions],
            xlim=(0, 22.5),
            ylim=(-0.6, 10.6),
            xlabel="Minutes after first probe completed",
            title=phase.capitalize(),
        )
        ax.xaxis.grid(alpha=0.15)
    fig.suptitle(
        "Browser references and repeat probes observe different moments",
        x=0.08,
        ha="left",
        fontweight="bold",
    )
    fig.text(
        0.08,
        0.865,
        "Each line joins the recorded browser-reference time to the repeat-probe start; these are elapsed times.",
        fontsize=9,
    )
    fig.text(
        0.08,
        0.045,
        "Blue: real datasets; orange: authored controls. Labels are agent inspections, not independent human or same-instant truth.",
        fontsize=8,
    )
    maps.append(finish(fig, list(axes), "observation_timing", items))
    (ROOT / "figures/value_map.json").write_bytes(
        (json.dumps(maps, indent=2) + "\n").encode()
    )
    print(
        json.dumps(
            dict(
                figures=3,
                formats=["pdf", "svg", "png"],
                plot_items=sum(len(m["items"]) for m in maps),
            )
        )
    )


if __name__ == "__main__":
    main()
