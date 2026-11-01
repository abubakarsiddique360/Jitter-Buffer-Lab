"""Text and JSON reporting."""

from __future__ import annotations

import json

from jitter_buffer_lab.buffer import adaptive, compare, fixed_delay, play_immediately
from jitter_buffer_lab.report import (
    render_compare,
    render_frontier,
    render_json,
    render_result,
    render_scenarios,
    render_trace,
)
from jitter_buffer_lab.trace import Packet, Trace


def _trace(offsets, interval=20.0, name="synthetic"):
    packets = tuple(
        Packet(
            index=index,
            sent_ms=index * interval,
            arrival_ms=None if offset is None else index * interval + offset,
            payload_bytes=960,
        )
        for index, offset in enumerate(offsets)
    )
    return Trace(
        name=name,
        interval_ms=interval,
        packets=packets,
        jitter_ms=4.0,
        loss=0.0,
        base_delay_ms=6.0,
    )


def test_render_trace_summarises_the_link():
    text = render_trace(_trace([4.0, 6.0, None, 8.0]))
    assert "Trace: synthetic" in text
    assert "packets            4 (0.1 s of audio at 20 ms)" in text
    assert "+/-4.0 ms" in text
    assert "25.00% (1 of 4)" in text
    assert "delay min/median/max" in text


def test_render_trace_omits_delay_stats_when_everything_is_lost():
    text = render_trace(_trace([None, None]))
    assert "delay min/median/max" not in text


def test_render_result_reports_latency_and_depth():
    text = render_result(fixed_delay(_trace([2.0] * 10), 40.0))
    assert "Policy: fixed 40ms" in text
    assert "underruns          0  (0.00%)" in text
    assert "buffer latency       40.0 ms mean" in text
    assert "max buffer depth   2 packets (40 ms of audio queued)" in text
    assert "longest hold       40.0 ms at packet" in text


def test_render_result_handles_a_policy_that_plays_nothing():
    text = render_result(fixed_delay(_trace([90.0] * 3), 0.0))
    assert "underruns          3  (100.00%)" in text
    assert "longest hold" not in text


def test_render_compare_lines_up_one_row_per_policy():
    results = compare(_trace([2.0] * 40 + [60.0]))
    text = render_compare(results)
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[0] == "Buffer policies on trace 'synthetic'"
    assert len(lines) == 2 + len(results) + 2
    assert "a policy is only better if it has fewer underruns" in text
    for result in results:
        assert result.policy[:23] in text


def test_render_compare_truncates_long_policy_names():
    text = render_compare([adaptive(_trace([2.0] * 10))])
    row = [line for line in text.splitlines() if line.startswith("adaptive")][0]
    assert row.startswith("adaptive (max +150ms)")


def test_render_scenarios_shows_every_row():
    rows = [
        fixed_delay(_trace([1.0]), 20.0).as_dict(),
        play_immediately(_trace([1.0])).as_dict(),
    ]
    text = render_scenarios(rows)
    assert "Every network, every policy" in text
    assert "passthrough" in text
    assert "fixed 20ms" in text


def test_render_frontier_shows_the_trade_curve():
    rows = [
        {"delay_ms": 0.0, "underruns": 100, "p95_ms": 0.0},
        {"delay_ms": 40.0, "underruns": 4, "p95_ms": 40.0},
    ]
    text = render_frontier(rows)
    assert "Latency against continuity for a fixed buffer" in text
    assert "           0        100          0.0" in text
    assert "          40          4         40.0" in text


def test_render_json_is_indented_json():
    payload = fixed_delay(_trace([1.0]), 20.0).as_dict()
    text = render_json(payload)
    assert json.loads(text) == payload
    assert "\n  " in text
