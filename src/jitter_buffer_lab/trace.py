"""Packet arrival traces for a real-time PCM stream.

A source packetises audio every ``interval_ms`` milliseconds. Each packet reaches the receiver
after a base network delay plus jitter, and some packets never arrive at all. That is the whole
model - it is deliberately small, because the interesting behaviour is in the receiver.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path


class TraceError(ValueError):
    """Raised when a trace cannot be built or read."""


@dataclass(frozen=True)
class Packet:
    index: int
    sent_ms: float
    arrival_ms: float | None
    payload_bytes: int

    @property
    def lost(self) -> bool:
        return self.arrival_ms is None

    @property
    def deadline_ms(self) -> float:
        """When the receiver must have this packet in order to play it on time."""
        return self.sent_ms

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "sent_ms": round(self.sent_ms, 3),
            "arrival_ms": None if self.arrival_ms is None else round(self.arrival_ms, 3),
            "payload_bytes": self.payload_bytes,
        }


@dataclass(frozen=True)
class Trace:
    name: str
    interval_ms: float
    packets: tuple[Packet, ...]
    jitter_ms: float
    loss: float
    base_delay_ms: float

    @property
    def duration_ms(self) -> float:
        return len(self.packets) * self.interval_ms

    @property
    def audio_bytes(self) -> int:
        return sum(packet.payload_bytes for packet in self.packets if not packet.lost)

    @property
    def loss_rate(self) -> float:
        if not self.packets:
            return 0.0
        return sum(1 for packet in self.packets if packet.lost) / len(self.packets)

    def arrivals(self) -> list[float | None]:
        return [packet.arrival_ms for packet in self.packets]

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "interval_ms": self.interval_ms,
            "jitter_ms": self.jitter_ms,
            "loss": self.loss,
            "base_delay_ms": self.base_delay_ms,
            "packets": [packet.as_dict() for packet in self.packets],
        }


def build_trace(
    *,
    name: str = "lan",
    packets: int = 1500,
    interval_ms: float = 20.0,
    base_delay_ms: float = 8.0,
    jitter_ms: float = 4.0,
    loss: float = 0.002,
    payload_bytes: int = 960,
    spike_every: int = 0,
    spike_ms: float = 0.0,
    seed: int = 7,
) -> Trace:
    """Generate a deterministic arrival trace.

    ``spike_every`` injects a rare, large delay (a retransmit, a queue drain) every N packets -
    the thing a fixed buffer sized to the average delay cannot survive.
    """
    if packets <= 0:
        raise TraceError("packet count must be positive")
    if interval_ms <= 0:
        raise TraceError("interval must be positive")
    if jitter_ms < 0:
        raise TraceError("jitter must be non-negative")
    if not 0 <= loss < 1:
        raise TraceError("loss must be in [0, 1)")
    if spike_every < 0:
        raise TraceError("spike_every must be non-negative")

    rng = random.Random(seed)
    built: list[Packet] = []
    for index in range(packets):
        sent = index * interval_ms
        if rng.random() < loss:
            built.append(Packet(index=index, sent_ms=sent, arrival_ms=None,
                                payload_bytes=payload_bytes))
            continue
        # Jitter is uniform in [-jitter, +jitter]: an early packet is as likely as a late one.
        offset = rng.uniform(-jitter_ms, jitter_ms)
        delay = max(0.0, base_delay_ms + offset)
        if spike_every and index and index % spike_every == 0:
            delay += spike_ms
        built.append(
            Packet(
                index=index,
                sent_ms=sent,
                arrival_ms=sent + delay,
                payload_bytes=payload_bytes,
            )
        )
    return Trace(
        name=name,
        interval_ms=interval_ms,
        packets=tuple(built),
        jitter_ms=jitter_ms,
        loss=loss,
        base_delay_ms=base_delay_ms,
    )


SCENARIOS = {
    "lan": dict(jitter_ms=2.0, loss=0.0005, base_delay_ms=3.0),
    "wifi": dict(jitter_ms=8.0, loss=0.004, base_delay_ms=6.0),
    "congested": dict(jitter_ms=14.0, loss=0.02, base_delay_ms=15.0),
    "spiky": dict(jitter_ms=3.0, loss=0.002, base_delay_ms=6.0, spike_every=120, spike_ms=45.0),
}


def load_trace(path: Path | str) -> Trace:
    source = Path(path)
    if not source.exists():
        raise TraceError(f"file not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TraceError(f"not valid JSON at line {exc.lineno}: {exc.msg}") from exc

    packets_raw = payload.get("packets")
    if not isinstance(packets_raw, list) or not packets_raw:
        raise TraceError("packets must be a non-empty list")
    packets: list[Packet] = []
    for expect, entry in enumerate(packets_raw):
        if not isinstance(entry, dict):
            raise TraceError(f"packet {expect} is not an object")
        index = int(entry.get("index", expect))
        sent = float(entry.get("sent_ms", math.nan))
        if math.isnan(sent):
            raise TraceError(f"packet {index} has no sent_ms")
        arrival_raw = entry.get("arrival_ms")
        arrival = None if arrival_raw is None else float(arrival_raw)
        packets.append(
            Packet(
                index=index,
                sent_ms=sent,
                arrival_ms=arrival,
                payload_bytes=int(entry.get("payload_bytes", 0)),
            )
        )
    return Trace(
        name=str(payload.get("name", source.stem)),
        interval_ms=float(payload.get("interval_ms", 20.0)),
        packets=tuple(packets),
        jitter_ms=float(payload.get("jitter_ms", 0.0)),
        loss=float(payload.get("loss", 0.0)),
        base_delay_ms=float(payload.get("base_delay_ms", 0.0)),
    )
