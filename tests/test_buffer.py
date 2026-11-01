"""Buffer policies and the playout simulation."""

from __future__ import annotations

import pytest

from jitter_buffer_lab.buffer import (
    COMPARE_ORDER,
    POLICIES,
    BufferError,
    Playout,
    Result,
    adaptive,
    compare,
    fixed_delay,
    latency_for_zero_underruns,
    measured_delay,
    play_immediately,
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
        jitter_ms=0.0,
        loss=0.0,
        base_delay_ms=0.0,
    )


def test_fixed_delay_plays_everything_that_arrives_in_time():
    trace = _trace([4.0, 9.0, 21.0, 5.0])
    result = fixed_delay(trace, 20.0)
    assert [playout.played for playout in result.playouts] == [True, True, False, True]
    assert result.underruns == 1


def test_fixed_delay_reports_the_hold_as_latency():
    result = fixed_delay(_trace([4.0] * 5), 20.0)
    assert result.mean_latency_ms == pytest.approx(20.0)
    assert result.percentile_latency(95) == pytest.approx(20.0)


def test_fixed_delay_zero_plays_only_packets_arriving_on_or_before_the_slot():
    result = fixed_delay(_trace([0.0, 0.5, -1.0]), 0.0)
    assert [playout.played for playout in result.playouts] == [True, False, True]


def test_fixed_delay_rejects_negative_delay():
    with pytest.raises(BufferError, match="non-negative"):
        fixed_delay(_trace([1.0]), -1.0)


def test_lost_packets_are_never_played_however_long_the_buffer():
    trace = _trace([None, None, 2.0])
    result = fixed_delay(trace, 500.0)
    assert result.underruns == 2
    assert result.playouts[0].arrival_ms is None
    assert result.playouts[0].lateness_ms == 0.0


def test_lateness_is_how_far_past_the_deadline_a_packet_arrived():
    result = fixed_delay(_trace([4.0, 30.0]), 10.0)
    late = result.playouts[1]
    assert late.played is False
    assert late.lateness_ms == pytest.approx(20.0)
    assert late.concealed is True


def test_passthrough_has_no_underruns_beyond_loss_and_zero_added_latency():
    trace = _trace([4.0, 9.0, None, 21.0])
    result = play_immediately(trace)
    assert result.policy == "passthrough"
    assert result.underruns == 1
    assert result.mean_latency_ms == pytest.approx((4.0 + 9.0 + 21.0) / 3)


def test_depth_counts_audio_queued_at_play_time():
    result = fixed_delay(_trace([2.0] * 10), 25.0)
    assert result.max_depth == 2


def test_empty_result_is_well_defined():
    empty = Result(policy="none", trace="t", playouts=(), interval_ms=20.0)
    assert empty.packets == 0
    assert empty.underruns == 0
    assert empty.underrun_rate == 0.0
    assert empty.mean_latency_ms == 0.0
    assert empty.percentile_latency(95) == 0.0
    assert empty.max_depth == 0


def test_result_as_dict_uses_stable_keys():
    payload = fixed_delay(_trace([2.0] * 4), 20.0).as_dict()
    assert payload == {
        "policy": "fixed 20ms",
        "trace": "synthetic",
        "packets": 4,
        "underruns": 0,
        "underrun_rate": 0.0,
        "mean_buffer_latency_ms": 20.0,
        "p95_buffer_latency_ms": 20.0,
        "max_depth_packets": 1,
    }


def test_playout_as_dict_rounds_and_keeps_none():
    playout = Playout(
        index=2, play_ms=41.23456, arrival_ms=None, played=False, depth=1, slot_ms=20.0
    )
    assert playout.as_dict() == {
        "index": 2,
        "play_ms": 41.235,
        "arrival_ms": None,
        "played": False,
        "buffer_ms": 1.235,
        "lateness_ms": 0.0,
        "depth": 1,
    }


def test_percentile_latency_walks_the_sorted_holds():
    result = fixed_delay(_trace([1.0, 1.0, 1.0, 1.0]), 20.0)
    assert result.percentile_latency(0) == pytest.approx(20.0)
    assert result.percentile_latency(100) == pytest.approx(20.0)


def test_measured_delay_sizes_from_the_tail_of_the_distribution():
    trace = _trace([1.0] * 99 + [50.0])
    p99 = measured_delay(trace, 99.0)
    assert p99.policy == "measured p99 (1ms)"
    assert p99.underruns == 1

    exact = measured_delay(trace, 100.0)
    assert exact.policy == "measured p100 (50ms)"
    assert exact.underruns == 0


def test_measured_delay_rejects_impossible_percentiles():
    trace = _trace([1.0])
    for bad in (0.0, -5.0, 101.0):
        with pytest.raises(BufferError, match="percentile"):
            measured_delay(trace, bad)


def test_measured_delay_needs_arrivals():
    with pytest.raises(BufferError, match="no arrivals"):
        measured_delay(_trace([None, None]))


def test_adaptive_pays_one_underrun_and_covers_the_rest_of_a_burst():
    trace = _trace([2.0] * 10 + [50.0] * 3 + [2.0] * 10)
    result = adaptive(trace, calm_packets=5)
    assert result.underruns == 1
    assert result.playouts[10].lateness_ms == pytest.approx(48.0)
    assert result.playouts[11].played is True


def test_adaptive_does_not_move_the_deadline_of_the_packet_that_missed_it():
    trace = _trace([2.0, 2.0, 60.0, 2.0])
    result = adaptive(trace)
    missed = result.playouts[2]
    assert missed.played is False
    assert missed.play_ms == pytest.approx(42.0)
    assert result.playouts[3].played is True


def test_adaptive_settles_to_the_links_minimum_and_stays_there():
    result = adaptive(_trace([2.0] * 40), calm_packets=5)
    assert result.underruns == 0
    assert result.mean_latency_ms == pytest.approx(2.0)


def test_adaptive_gives_latency_back_after_a_long_calm_spell():
    trace = _trace([2.0] * 20 + [90.0] + [2.0] * 400)
    result = adaptive(trace, step_ms=5.0, calm_packets=5)
    early = result.playouts[21:120]
    late = result.playouts[-100:]
    assert sum(p.buffer_ms for p in late) / len(late) < sum(p.buffer_ms for p in early) / len(early)


def test_adaptive_respects_the_max_extra_cap():
    result = adaptive(_trace([2.0] * 5 + [400.0] + [2.0] * 5), max_extra_ms=20.0)
    assert max(playout.buffer_ms for playout in result.playouts) <= 20.0


def test_adaptive_honours_a_floor():
    result = adaptive(_trace([2.0] * 10), floor_ms=15.0)
    assert result.mean_latency_ms >= 15.0
    assert result.underruns == 0


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"step_ms": 0.0}, "step"),
        ({"max_extra_ms": -1.0}, "max_extra"),
        ({"calm_packets": 0}, "calm_packets"),
    ],
)
def test_adaptive_rejects_bad_parameters(kwargs, message):
    with pytest.raises(BufferError, match=message):
        adaptive(_trace([2.0]), **kwargs)


def test_latency_for_zero_underruns_finds_the_smallest_workable_delay():
    trace = _trace([5.0, 12.0, 3.0, 9.0, 14.0])
    assert latency_for_zero_underruns(trace) == pytest.approx(14.0)


def test_latency_for_zero_underruns_ignores_lost_packets():
    trace = _trace([3.0, None, 4.0, None])
    assert latency_for_zero_underruns(trace) == pytest.approx(4.0)


def test_latency_for_zero_underruns_returns_none_when_the_limit_is_too_low():
    assert latency_for_zero_underruns(_trace([50.0]), limit_ms=10.0) is None


def test_latency_for_zero_underruns_rejects_a_bad_step():
    with pytest.raises(BufferError, match="step"):
        latency_for_zero_underruns(_trace([1.0]), step_ms=0.0)


def test_compare_covers_every_real_time_policy_in_order():
    results = compare(_trace([5.0] * 20))
    assert tuple(result.policy for result in results) == (
        "fixed 0ms",
        "fixed 20ms",
        "fixed 50ms",
        "measured p99 (5ms)",
        results[-1].policy,
    )
    assert len(COMPARE_ORDER) == len(POLICIES) - 1
    assert "passthrough" in POLICIES


def test_compare_rejects_an_unknown_policy():
    with pytest.raises(BufferError, match="unknown policy"):
        compare(_trace([1.0]), ("nope",))


def test_compare_accepts_an_explicit_selection():
    results = compare(_trace([1.0] * 4), ("fixed-20",))
    assert [result.policy for result in results] == ["fixed 20ms"]


def test_the_naive_fixed_clock_collapses_under_any_real_delay():
    trace = _trace([6.0] * 50)
    assert fixed_delay(trace, 0.0).underrun_rate == 1.0
