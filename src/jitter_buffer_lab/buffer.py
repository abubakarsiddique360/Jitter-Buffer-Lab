"""Jitter-buffer policies and the playout simulation.

A buffer trades latency for continuity: wait longer and fewer packets are late, wait less and the
audio is closer to real time but drops out. Every policy here answers one question - **at what
absolute time do I play packet i** - and the simulation asks the only question that matters: had it
arrived by then?
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .trace import Trace


class BufferError(ValueError):
    """Raised for invalid buffer parameters."""


@dataclass(frozen=True)
class Playout:
    """What the receiver did with one packet."""

    index: int
    play_ms: float
    arrival_ms: float | None
    played: bool
    depth: int
    slot_ms: float

    @property
    def concealed(self) -> bool:
        return not self.played

    @property
    def buffer_ms(self) -> float:
        """Delay the buffer added between the packet's nominal slot and its actual play time."""
        return self.play_ms - self.index * self.slot_ms

    @property
    def lateness_ms(self) -> float:
        """How late the packet would have been, in ms. Zero when it made the deadline."""
        if self.arrival_ms is None:
            return 0.0
        return max(0.0, self.arrival_ms - self.play_ms)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "play_ms": round(self.play_ms, 3),
            "arrival_ms": None if self.arrival_ms is None else round(self.arrival_ms, 3),
            "played": self.played,
            "buffer_ms": round(self.buffer_ms, 3),
            "lateness_ms": round(self.lateness_ms, 3),
            "depth": self.depth,
        }


@dataclass(frozen=True)
class Result:
    policy: str
    trace: str
    playouts: tuple[Playout, ...]
    interval_ms: float

    @property
    def packets(self) -> int:
        return len(self.playouts)

    @property
    def underruns(self) -> int:
        return sum(1 for playout in self.playouts if not playout.played)

    @property
    def underrun_rate(self) -> float:
        return self.underruns / self.packets if self.packets else 0.0

    @property
    def mean_latency_ms(self) -> float:
        """Mean delay the buffer added, over packets that were actually played."""
        values = [playout.buffer_ms for playout in self.playouts if playout.played]
        return sum(values) / len(values) if values else 0.0

    def percentile_latency(self, percentile: float = 95.0) -> float:
        values = sorted(playout.buffer_ms for playout in self.playouts if playout.played)
        if not values:
            return 0.0
        index = min(len(values) - 1, int(round(percentile / 100 * (len(values) - 1))))
        return values[index]

    @property
    def max_depth(self) -> int:
        return max((playout.depth for playout in self.playouts), default=0)

    def as_dict(self) -> dict:
        return {
            "policy": self.policy,
            "trace": self.trace,
            "packets": self.packets,
            "underruns": self.underruns,
            "underrun_rate": round(self.underrun_rate, 5),
            "mean_buffer_latency_ms": round(self.mean_latency_ms, 3),
            "p95_buffer_latency_ms": round(self.percentile_latency(95), 3),
            "max_depth_packets": self.max_depth,
        }


def _simulate(trace: Trace, policy: str, play_times: list[float]) -> Result:
    """Judge one schedule: a packet is played only if it had arrived by its play time."""
    arrivals = sorted(
        packet.arrival_ms for packet in trace.packets if packet.arrival_ms is not None
    )
    playouts: list[Playout] = []
    for index, packet in enumerate(trace.packets):
        play = play_times[index]
        arrival = packet.arrival_ms
        played = arrival is not None and arrival <= play
        playouts.append(
            Playout(
                index=index,
                play_ms=play,
                arrival_ms=arrival,
                played=played,
                depth=max(0, bisect_right(arrivals, play) - index),
                slot_ms=trace.interval_ms,
            )
        )
    return Result(
        policy=policy,
        trace=trace.name,
        playouts=tuple(playouts),
        interval_ms=trace.interval_ms,
    )


def play_immediately(trace: Trace) -> Result:
    """Passthrough: play each packet the moment it arrives, with no clock at all.

    This is the floor on latency and not a real-time policy - the playout clock is driven by the
    network, so every millisecond of jitter becomes a millisecond of audio distortion. It is in the
    tool because it is the reference point for "how much latency is the buffer adding": nothing.
    """
    times = [
        packet.arrival_ms if packet.arrival_ms is not None else packet.sent_ms + trace.interval_ms
        for packet in trace.packets
    ]
    return _simulate(trace, "passthrough", times)


def fixed_delay(trace: Trace, delay_ms: float) -> Result:
    """Wait a constant ``delay_ms`` after the nominal slot.

    Sized from the *average* delay this looks fine and still drops every spike, which is why the
    tail of the delay distribution is what a buffer should be sized from.
    """
    if delay_ms < 0:
        raise BufferError("delay must be non-negative")
    return _simulate(
        trace, f"fixed {delay_ms:g}ms", [packet.sent_ms + delay_ms for packet in trace.packets]
    )


def measured_delay(trace: Trace, percentile: float = 99.0) -> Result:
    """Size a fixed buffer from the measured delay distribution.

    A receiver can measure the jitter of a link and pick, say, the 99th percentile of the relative
    delay. That is exactly a fixed buffer with its delay chosen by data instead of by guess - more
    latency than the mean, far less than the worst spike.
    """
    if not 0 < percentile <= 100:
        raise BufferError("percentile must be in (0, 100]")
    offsets = sorted(
        packet.arrival_ms - packet.sent_ms
        for packet in trace.packets
        if packet.arrival_ms is not None
    )
    if not offsets:
        raise BufferError("trace has no arrivals to measure")
    rank = min(len(offsets) - 1, int(percentile / 100 * (len(offsets) - 1)))
    target = offsets[rank]
    result = fixed_delay(trace, target)
    return Result(
        policy=f"measured p{percentile:g} ({target:.0f}ms)",
        trace=result.trace,
        playouts=result.playouts,
        interval_ms=result.interval_ms,
    )


def adaptive(
    trace: Trace,
    *,
    floor_ms: float = 0.0,
    step_ms: float = 1.0,
    max_extra_ms: float = 150.0,
    calm_packets: int = 400,
) -> Result:
    """Jump the delay when a packet is late, give it back only when there is headroom to spare.

    This is what a real adaptive jitter buffer does: react immediately (an underrun is audible) and
    recover slowly (a small latency change is not). Three details make it behave like hardware
    rather than like a spreadsheet:

    * the first arrival prefills the buffer, because a receiver does not start its clock before it
      has any audio;
    * a late packet is still late - the jump sizes *future* playout, it does not retroactively save
      the packet that missed its slot, which is why the cost is roughly one underrun per burst;
    * a step of delay is only released once ``calm_packets`` packets in a row had more than one
      step of headroom, which stops the buffer oscillating around the link's real minimum.
    """
    if step_ms <= 0:
        raise BufferError("step must be positive")
    if max_extra_ms < 0:
        raise BufferError("max_extra must be non-negative")
    if calm_packets < 1:
        raise BufferError("calm_packets must be at least 1")

    extra = 0.0
    calm = 0
    prefilled = False
    times: list[float] = []
    for packet in trace.packets:
        arrival = packet.arrival_ms
        base = packet.sent_ms + floor_ms
        if not prefilled and arrival is not None:
            extra = min(max_extra_ms, max(0.0, arrival - base))
            prefilled = True

        play = base + extra
        times.append(play)
        if arrival is None:
            calm = 0
            continue
        if arrival > play:
            extra = min(max_extra_ms, extra + (arrival - play) + step_ms)
            calm = 0
        elif play - arrival > step_ms:
            calm += 1
            if calm >= calm_packets and extra > 0:
                extra = max(0.0, extra - step_ms)
                calm = 0
        else:
            calm = 0

    return _simulate(trace, f"adaptive (max +{max_extra_ms:g}ms)", times)


POLICIES = {
    "clock-only": lambda trace: fixed_delay(trace, 0.0),
    "fixed-20": lambda trace: fixed_delay(trace, 20.0),
    "fixed-50": lambda trace: fixed_delay(trace, 50.0),
    "measured-p99": lambda trace: measured_delay(trace, 99.0),
    "adaptive": adaptive,
    "passthrough": play_immediately,
}

#: The receivers that actually keep a playout clock. ``passthrough`` is deliberately excluded: it
#: has no underruns to speak of because it has no clock to underrun, and it belongs in the latency
#: discussion, not the continuity one.
COMPARE_ORDER = ("clock-only", "fixed-20", "fixed-50", "measured-p99", "adaptive")


def compare(trace: Trace, names: tuple[str, ...] | None = None) -> list[Result]:
    chosen = names or COMPARE_ORDER
    results = []
    for name in chosen:
        if name not in POLICIES:
            raise BufferError(f"unknown policy {name!r}")
        results.append(POLICIES[name](trace))
    return results


def latency_for_zero_underruns(
    trace: Trace, *, step_ms: float = 1.0, limit_ms: float = 400.0
) -> float | None:
    """Smallest fixed delay that plays every packet that was not lost.

    This is the honest answer to "what does perfect continuity cost on this link" - for the packets
    that arrived. A lost packet is lost at any buffer size, which is why the loss rate is reported
    next to it rather than folded into the underrun count.
    """
    if step_ms <= 0:
        raise BufferError("step must be positive")
    target = sum(1 for packet in trace.packets if packet.arrival_ms is not None)
    delay = 0.0
    while delay <= limit_ms:
        result = fixed_delay(trace, delay)
        if sum(1 for playout in result.playouts if playout.played) >= target:
            return delay
        delay += step_ms
    return None
