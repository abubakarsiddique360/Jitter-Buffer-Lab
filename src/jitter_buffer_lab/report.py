"""Text reports for jitter-buffer results."""

from __future__ import annotations

import json

from .buffer import Result
from .trace import Trace


def render_result(result: Result) -> str:
    played = [playout for playout in result.playouts if playout.played]
    lines = [f"Policy: {result.policy}", "=" * 62]
    lines.append(f"packets            {result.packets}")
    lines.append(f"underruns          {result.underruns}  ({result.underrun_rate * 100:.2f}%)")
    lines.append(f"buffer latency     {result.mean_latency_ms:6.1f} ms mean, "
                 f"{result.percentile_latency(95):6.1f} ms p95")
    lines.append(f"max buffer depth   {result.max_depth} packets "
                 f"({result.max_depth * result.interval_ms:.0f} ms of audio queued)")
    if played:
        worst = max(played, key=lambda playout: playout.buffer_ms)
        lines.append(f"longest hold       {worst.buffer_ms:.1f} ms at packet {worst.index}")
    return "\n".join(lines)


def render_trace(trace: Trace) -> str:
    offsets = [
        packet.arrival_ms - packet.sent_ms
        for packet in trace.packets
        if packet.arrival_ms is not None
    ]
    offsets.sort()
    lines = [f"Trace: {trace.name}", "=" * 62]
    lines.append(f"packets            {len(trace.packets)} "
                 f"({trace.duration_ms / 1000:.1f} s of audio at {trace.interval_ms:g} ms)")
    lines.append(f"base delay         {trace.base_delay_ms:.1f} ms")
    lines.append(f"jitter             +/-{trace.jitter_ms:.1f} ms")
    lines.append(f"loss               {trace.loss_rate * 100:.2f}% "
                 f"({sum(1 for p in trace.packets if p.lost)} of {len(trace.packets)})")
    if offsets:
        lines.append(f"delay min/median/max  {offsets[0]:.1f} / "
                     f"{offsets[len(offsets) // 2]:.1f} / {offsets[-1]:.1f} ms")
    return "\n".join(lines)


def render_compare(results: list[Result]) -> str:
    header = (
        f"{'policy':<24}{'underruns':>10}{'rate':>8}{'mean ms':>9}{'p95 ms':>8}{'depth':>7}"
    )
    lines = [f"Buffer policies on trace '{results[0].trace}'", "=" * len(header), header]
    for result in results:
        lines.append(
            f"{result.policy[:23]:<24}{result.underruns:>10}"
            f"{result.underrun_rate * 100:>7.2f}%{result.mean_latency_ms:>9.1f}"
            f"{result.percentile_latency(95):>8.1f}{result.max_depth:>7}"
        )
    lines.append("")
    lines.append("a policy is only better if it has fewer underruns at comparable latency")
    return "\n".join(lines)


def render_scenarios(rows: list[dict]) -> str:
    header = (
        f"{'trace':<12}{'policy':<24}{'underruns':>10}{'rate':>8}{'mean ms':>9}{'p95 ms':>8}"
    )
    lines = ["Every network, every policy", "=" * len(header), header]
    for row in rows:
        lines.append(
            f"{row['trace'][:11]:<12}{row['policy'][:23]:<24}{row['underruns']:>10}"
            f"{row['underrun_rate'] * 100:>7.2f}%{row['mean_buffer_latency_ms']:>9.1f}"
            f"{row['p95_buffer_latency_ms']:>8.1f}"
        )
    return "\n".join(lines)


def render_frontier(rows: list[dict]) -> str:
    header = f"{'fixed delay':>12}{'underruns':>11}{'p95 latency':>13}"
    lines = ["Latency against continuity for a fixed buffer", "=" * len(header), header]
    for row in rows:
        lines.append(
            f"{row['delay_ms']:>12.0f}{row['underruns']:>11}{row['p95_ms']:>13.1f}"
        )
    return "\n".join(lines)


def render_json(payload) -> str:
    return json.dumps(payload, indent=2)
