"""Packet trace generation and parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jitter_buffer_lab.trace import (
    SCENARIOS,
    Packet,
    Trace,
    TraceError,
    build_trace,
    load_trace,
)


def _trace(offsets, interval=20.0):
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
        name="synthetic",
        interval_ms=interval,
        packets=packets,
        jitter_ms=0.0,
        loss=0.0,
        base_delay_ms=0.0,
    )


def test_packet_marks_loss_and_deadline():
    lost = Packet(index=3, sent_ms=60.0, arrival_ms=None, payload_bytes=960)
    assert lost.lost is True
    assert lost.deadline_ms == 60.0
    assert lost.as_dict()["arrival_ms"] is None


def test_packet_as_dict_rounds_floats():
    packet = Packet(index=1, sent_ms=20.0, arrival_ms=26.6666, payload_bytes=960)
    assert packet.as_dict() == {
        "index": 1,
        "sent_ms": 20.0,
        "arrival_ms": 26.667,
        "payload_bytes": 960,
    }


def test_trace_duration_and_loss_rate():
    trace = _trace([4.0, None, 5.0, None])
    assert trace.duration_ms == 80.0
    assert trace.loss_rate == 0.5
    assert trace.arrivals() == [4.0, None, 45.0, None]


def test_trace_audio_bytes_ignores_lost_packets():
    assert _trace([4.0, None]).audio_bytes == 960


def test_empty_trace_has_zero_loss_rate():
    trace = Trace(
        name="empty",
        interval_ms=20.0,
        packets=(),
        jitter_ms=0.0,
        loss=0.0,
        base_delay_ms=0.0,
    )
    assert trace.loss_rate == 0.0
    assert trace.duration_ms == 0.0


def test_build_trace_is_deterministic_for_a_seed():
    first = build_trace(packets=50, seed=11)
    second = build_trace(packets=50, seed=11)
    third = build_trace(packets=50, seed=12)
    assert first.arrivals() == second.arrivals()
    assert first.arrivals() != third.arrivals()


def test_build_trace_arrivals_never_precede_sends():
    trace = build_trace(packets=200, jitter_ms=40.0, base_delay_ms=1.0, seed=3)
    for packet in trace.packets:
        if packet.arrival_ms is not None:
            assert packet.arrival_ms >= packet.sent_ms


def test_build_trace_spike_extends_the_tail():
    flat = build_trace(packets=240, jitter_ms=1.0, base_delay_ms=5.0, seed=5)
    spiky = build_trace(
        packets=240, jitter_ms=1.0, base_delay_ms=5.0, seed=5, spike_every=120, spike_ms=45.0
    )
    for trace in (flat, spiky):
        assert len(trace.packets) == 240
    flat_at = flat.packets[120]
    spike_at = spiky.packets[120]
    assert spike_at.arrival_ms == pytest.approx(flat_at.arrival_ms + 45.0)
    flat_max = max(p.arrival_ms - p.sent_ms for p in flat.packets if p.arrival_ms is not None)
    spiky_max = max(p.arrival_ms - p.sent_ms for p in spiky.packets if p.arrival_ms is not None)
    assert spiky_max > flat_max + 40.0


def test_build_trace_loss_is_actually_applied():
    trace = build_trace(packets=400, loss=0.25, seed=9)
    assert 0.15 < trace.loss_rate < 0.35


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"packets": 0}, "packet count"),
        ({"interval_ms": 0.0}, "interval"),
        ({"jitter_ms": -1.0}, "jitter"),
        ({"loss": 1.0}, "loss"),
        ({"loss": -0.1}, "loss"),
        ({"spike_every": -2}, "spike_every"),
    ],
)
def test_build_trace_rejects_bad_input(kwargs, message):
    with pytest.raises(TraceError, match=message):
        build_trace(**kwargs)


def test_scenarios_are_all_buildable():
    assert set(SCENARIOS) == {"lan", "wifi", "congested", "spiky"}
    for name, overrides in SCENARIOS.items():
        trace = build_trace(name=name, packets=30, **overrides)
        assert trace.name == name
        assert len(trace.packets) == 30


def test_load_trace_round_trips_a_saved_file(tmp_path: Path):
    original = build_trace(name="roundtrip", packets=40, seed=4)
    target = tmp_path / "trace.json"
    target.write_text(json.dumps(original.as_dict()), encoding="utf-8")

    loaded = load_trace(target)
    assert loaded.name == "roundtrip"
    assert loaded.interval_ms == original.interval_ms
    for saved, replay in zip(loaded.arrivals(), original.arrivals(), strict=True):
        assert saved == pytest.approx(replay, abs=1e-3)


def test_load_trace_missing_file(tmp_path: Path):
    with pytest.raises(TraceError, match="file not found"):
        load_trace(tmp_path / "nope.json")


def test_load_trace_invalid_json(tmp_path: Path):
    target = tmp_path / "broken.json"
    target.write_text("{oops", encoding="utf-8")
    with pytest.raises(TraceError, match="not valid JSON"):
        load_trace(target)


def test_load_trace_requires_packets(tmp_path: Path):
    target = tmp_path / "empty.json"
    target.write_text(json.dumps({"packets": []}), encoding="utf-8")
    with pytest.raises(TraceError, match="non-empty list"):
        load_trace(target)


def test_load_trace_rejects_non_object_packet(tmp_path: Path):
    target = tmp_path / "shape.json"
    target.write_text(json.dumps({"packets": [1, 2]}), encoding="utf-8")
    with pytest.raises(TraceError, match="not an object"):
        load_trace(target)


def test_load_trace_requires_sent_ms(tmp_path: Path):
    target = tmp_path / "nosent.json"
    target.write_text(json.dumps({"packets": [{"index": 0}]}), encoding="utf-8")
    with pytest.raises(TraceError, match="no sent_ms"):
        load_trace(target)


def test_load_trace_fills_defaults(tmp_path: Path):
    target = tmp_path / "minimal.json"
    target.write_text(
        json.dumps({"packets": [{"sent_ms": 0, "arrival_ms": 5}]}), encoding="utf-8"
    )
    trace = load_trace(target)
    assert trace.name == "minimal"
    assert trace.interval_ms == 20.0
    assert trace.packets[0].payload_bytes == 0
