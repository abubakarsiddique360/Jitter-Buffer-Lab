"""Regenerate README assets from real runs.

Usage:  python scripts/render_assets.py
Writes: docs/results.json, docs/images/workbench.png,
        docs/images/tradeoff.png, docs/images/playout-timeline.png
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jitter_buffer_lab.buffer import (  # noqa: E402
    adaptive,
    compare,
    fixed_delay,
    latency_for_zero_underruns,
    measured_delay,
)
from jitter_buffer_lab.report import render_compare, render_scenarios, render_trace  # noqa: E402
from jitter_buffer_lab.trace import SCENARIOS, build_trace, load_trace  # noqa: E402

BG = "#0d1117"
CHROME = "#161b22"
FG = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#79c0ff"

SCENARIO_COLOURS = {
    "lan": "#1a7f37",
    "wifi": "#0969da",
    "congested": "#bf8700",
    "spiky": "#cf222e",
}


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("consola.ttf", "courier.ttf", "DejaVuSansMono.ttf"):
        candidate = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def render_terminal(text: str, out_path: Path, title: str) -> None:
    font = load_font(13)
    lines = text.expandtabs(4).splitlines() or [""]
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    char_width = probe.textlength("M", font=font)
    line_height = 19
    pad = 24
    width = int(max(len(line) for line in lines) * char_width) + pad * 2
    height = pad * 2 + len(lines) * line_height + 44
    image = Image.new("RGB", (max(width, 820), height), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, image.width, 34], fill=CHROME)
    for index, colour in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        draw.ellipse([16 + index * 22, 10, 28 + index * 22, 22], fill=colour)
    draw.text((92, 9), title, font=load_font(12), fill=MUTED)

    for row, line in enumerate(lines):
        colour = FG
        stripped = line.strip()
        if stripped.startswith("Trace:") or stripped.startswith("Buffer policies"):
            colour = ACCENT
        elif stripped.startswith(("lan", "wifi", "congested", "spiky")):
            colour = SCENARIO_COLOURS.get(stripped.split()[0], FG)
        elif stripped.startswith(("policy", "trace", "=", "packets", "base delay", "jitter",
                                 "loss", "delay")):
            colour = MUTED
        elif "a policy is only better" in stripped:
            colour = MUTED
        draw.text((pad, 50 + row * line_height), line, font=font, fill=colour)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def render_tradeoff(traces: dict, out_path: Path) -> dict:
    """Left: the fixed-delay frontier per link. Right: what perfect continuity costs."""
    figure, (left, right) = plt.subplots(1, 2, figsize=(11.6, 4.0),
                                         gridspec_kw={"width_ratios": [1.35, 1.0]})
    figure.patch.set_facecolor("#ffffff")

    required = {}
    for name, trace in traces.items():
        colour = SCENARIO_COLOURS[name]
        delays, rates = [], []
        delay = 0.0
        while delay <= 64.0:
            result = fixed_delay(trace, delay)
            delays.append(delay)
            rates.append(max(result.underrun_rate * 100, 0.02))
            delay += 2.0
        left.plot(delays, rates, color=colour, linewidth=1.6, label=name)
        auto = adaptive(trace)
        left.scatter([auto.mean_latency_ms], [max(auto.underrun_rate * 100, 0.02)],
                     color=colour, edgecolor="#24292f", s=70, marker="*", zorder=5)
        measured = measured_delay(trace, 99.0)
        left.scatter([measured.mean_latency_ms], [max(measured.underrun_rate * 100, 0.02)],
                     color=colour, s=26, marker="o", alpha=0.75, zorder=4)
        required[name] = latency_for_zero_underruns(trace)

    left.set_yscale("log")
    left.set_xlabel("mean buffer latency added (ms)")
    left.set_ylabel("packets that missed their slot (%)")
    left.set_title("Every fixed buffer is a point on this curve", fontsize=10, loc="left")
    left.grid(True, which="both", linestyle=":", alpha=0.45)
    left.spines["top"].set_visible(False)
    left.spines["right"].set_visible(False)
    handles, labels = left.get_legend_handles_labels()
    handles.append(plt.Line2D([], [], color="#57606a", linestyle="none", marker="*",
                              markersize=9, label="adaptive"))
    handles.append(plt.Line2D([], [], color="#57606a", linestyle="none", marker="o",
                              markersize=5, label="measured p99"))
    left.legend(handles=handles, labels=[*labels, "adaptive", "measured p99"],
                fontsize=7.5, frameon=False, ncol=2)

    names = list(traces)
    positions = range(len(names))
    floor_values = [required[name] if required[name] is not None else 0.0 for name in names]
    auto_values = [adaptive(traces[name]).mean_latency_ms for name in names]
    width = 0.36
    bars = right.bar([p - width / 2 for p in positions], floor_values, width,
                     color="#0969da", label="smallest fixed buffer with no avoidable underruns")
    right.bar([p + width / 2 for p in positions], auto_values, width,
              color="#1a7f37", label="adaptive, mean latency")
    for bar, value in zip(bars, floor_values, strict=True):
        right.text(bar.get_x() + bar.get_width() / 2, value + 1.2, f"{value:.0f}",
                   ha="center", fontsize=7.5, color="#0969da")
    for position, value in zip(positions, auto_values, strict=True):
        right.text(position + width / 2, value + 1.2, f"{value:.0f}", ha="center",
                   fontsize=7.5, color="#1a7f37")
    right.set_xticks(list(positions))
    right.set_xticklabels(names, fontsize=8.5)
    right.set_ylabel("milliseconds")
    right.set_ylim(0, max(floor_values + auto_values) * 1.22)
    right.set_title("What continuity costs on each link", fontsize=10, loc="left")
    right.grid(axis="y", linestyle=":", alpha=0.45)
    right.spines["top"].set_visible(False)
    right.spines["right"].set_visible(False)
    right.legend(fontsize=7.5, frameon=False, loc="upper left")

    figure.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out_path, format="png", dpi=150)
    plt.close(figure)
    return required


def render_timeline(trace, out_path: Path) -> dict:
    result = adaptive(trace)
    window = range(60, 480)
    offsets = []
    for index in window:
        packet = trace.packets[index]
        arrival = packet.arrival_ms
        offsets.append(None if arrival is None else arrival - packet.sent_ms)
    holds = [result.playouts[index].buffer_ms for index in window]
    missed = [result.playouts[index].played is False for index in window]
    tallest = max(value for value in offsets if value is not None)

    figure, axis = plt.subplots(figsize=(11.2, 3.8))
    figure.patch.set_facecolor("#ffffff")
    axis.vlines(list(window), 0,
                [value if value is not None else 0.0 for value in offsets],
                color="#a5d6ff", linewidth=0.9, label="when each packet actually arrived")
    axis.step(list(window), holds, where="post", color="#cf222e", linewidth=1.7,
              label="how long the adaptive buffer held it")
    axis.scatter([index for index, bad in zip(window, missed, strict=True) if bad],
                 [-2.0] * sum(missed), marker="x", color="#82071e", s=34, zorder=6,
                 label="concealed (played nothing)")
    axis.annotate("first 45 ms spike:\none packet lost, buffer jumps once",
                  xy=(121, holds[61]), xytext=(150, tallest * 1.45),
                  fontsize=8, color="#24292f",
                  arrowprops={"arrowstyle": "->", "color": "#57606a", "linewidth": 0.9})
    axis.annotate("later spikes cost nothing", xy=(360, tallest * 1.02),
                  xytext=(300, tallest * 1.75), fontsize=8, color="#24292f",
                  arrowprops={"arrowstyle": "->", "color": "#57606a", "linewidth": 0.9})
    axis.set_xlabel("packet index (one packet = 20 ms of audio)")
    axis.set_ylabel("milliseconds after the nominal slot")
    axis.set_title("Adaptive buffer on the spiky link: jump once, then absorb the rest",
                   fontsize=10, loc="left")
    axis.set_ylim(-6, tallest * 2.05)
    axis.set_xlim(window.start - 4, window.stop + 4)
    axis.grid(True, linestyle=":", alpha=0.45)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.legend(fontsize=8, frameon=False, loc="upper center", ncol=3,
                bbox_to_anchor=(0.5, -0.22))
    figure.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out_path, format="png", dpi=150, bbox_inches="tight")
    plt.close(figure)
    return {
        "packets_shown": len(window),
        "max_hold_ms": round(max(holds), 2),
        "concealed_in_window": sum(missed),
    }


def main() -> int:
    images = ROOT / "docs" / "images"
    images.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)

    traces = {
        name: build_trace(name=name, packets=1500, seed=7, **overrides)
        for name, overrides in SCENARIOS.items()
    }
    captured = load_trace("examples/wifi-capture.json")

    rows = [
        result.as_dict()
        for trace in traces.values()
        for result in compare(trace)
    ]
    console = "\n".join(
        [
            render_trace(traces["spiky"]),
            "",
            render_compare(compare(traces["spiky"])),
            "",
            render_scenarios(rows),
        ]
    )
    render_terminal(console, images / "workbench.png", "jitter-lab compare --scenario spiky")
    required = render_tradeoff(traces, images / "tradeoff.png")
    timeline = render_timeline(traces["spiky"], images / "playout-timeline.png")

    payload = {
        "trace_packets": 1500,
        "interval_ms": 20.0,
        "scenarios": {
            name: {
                "generator": overrides,
                "loss_rate": round(traces[name].loss_rate, 5),
                "smallest_fixed_delay_for_no_avoidable_underruns_ms": required[name],
                "policies": [result.as_dict() for result in compare(traces[name])],
            }
            for name, overrides in SCENARIOS.items()
        },
        "captured_example": {
            "file": "examples/wifi-capture.json",
            "packets": len(captured.packets),
            "policies": [result.as_dict() for result in compare(captured)],
        },
        "timeline": timeline,
    }
    (ROOT / "docs" / "results.json").write_text(json.dumps(payload, indent=2) + "\n",
                                                encoding="utf-8")

    for name in ("workbench.png", "tradeoff.png", "playout-timeline.png"):
        print(f"wrote docs/images/{name}")
    print(f"wrote docs/results.json ({len(json.dumps(payload))} bytes of JSON)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
