"""Command line entry point for jitter-lab."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .buffer import (
    POLICIES,
    BufferError,
    adaptive,
    compare,
    fixed_delay,
    latency_for_zero_underruns,
    measured_delay,
    play_immediately,
)
from .report import (
    render_compare,
    render_frontier,
    render_json,
    render_result,
    render_scenarios,
    render_trace,
)
from .trace import SCENARIOS, TraceError, build_trace, load_trace

DEFAULT_PACKETS = 1500

FIXED_PRESETS = {"clock-only": 0.0, "fixed-20": 20.0, "fixed-50": 50.0}


def _add_source(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), default="wifi")
    parser.add_argument("--trace-file", type=Path, default=None,
                        help="read a captured trace instead of generating one")
    parser.add_argument("--packets", type=int, default=DEFAULT_PACKETS)
    parser.add_argument("--seed", type=int, default=7)


def _load(args):
    if getattr(args, "trace_file", None) is not None:
        return load_trace(args.trace_file)
    if args.scenario not in SCENARIOS:
        raise TraceError(f"unknown scenario {args.scenario!r}")
    return build_trace(name=args.scenario, packets=args.packets, seed=args.seed,
                       **SCENARIOS[args.scenario])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jitter-lab",
        description="Measure jitter-buffer policies against real-time PCM packet arrivals.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    compare_cmd = sub.add_parser("compare", help="all policies on one trace")
    _add_source(compare_cmd)
    compare_cmd.add_argument("--format", choices=("text", "json"), default="text")

    trace_cmd = sub.add_parser("trace", help="summarise a trace")
    _add_source(trace_cmd)
    trace_cmd.add_argument("--format", choices=("text", "json"), default="text")

    one = sub.add_parser("run", help="one policy on one trace")
    _add_source(one)
    one.add_argument("--policy", choices=tuple(POLICIES), default="adaptive")
    one.add_argument("--delay-ms", type=float, default=None,
                     help="override the hold time of fixed-20 / fixed-50")
    one.add_argument("--format", choices=("text", "json"), default="text")

    grid = sub.add_parser("scenarios", help="every scenario against every policy")
    grid.add_argument("--packets", type=int, default=DEFAULT_PACKETS)
    grid.add_argument("--seed", type=int, default=7)
    grid.add_argument("--format", choices=("text", "json"), default="text")

    front = sub.add_parser("frontier", help="underruns against delay for a fixed buffer")
    _add_source(front)
    front.add_argument("--step", type=float, default=5.0)
    front.add_argument("--max-ms", type=float, default=120.0)
    front.add_argument("--format", choices=("text", "json"), default="text")

    need = sub.add_parser("required", help="smallest fixed delay with no avoidable underruns")
    _add_source(need)
    need.add_argument("--max-ms", type=float, default=400.0)

    save = sub.add_parser("save", help="write a generated trace as JSON")
    _add_source(save)
    save.add_argument("--out", required=True, type=Path)
    return parser


def _run_policy(trace, args):
    if args.policy in FIXED_PRESETS:
        delay = args.delay_ms
        return fixed_delay(trace, FIXED_PRESETS[args.policy] if delay is None else delay)
    if args.policy == "measured-p99":
        return measured_delay(trace, 99.0)
    if args.policy == "adaptive":
        return adaptive(trace)
    return play_immediately(trace)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "trace":
            trace = _load(args)
            if args.format == "json":
                print(render_json(trace.as_dict() | {"packets": len(trace.packets)}))
            else:
                print(render_trace(trace))
            return 0

        if args.command == "compare":
            trace = _load(args)
            results = compare(trace)
            if args.format == "json":
                print(render_json([result.as_dict() for result in results]))
            else:
                print(render_trace(trace))
                print()
                print(render_compare(results))
            return 0

        if args.command == "run":
            trace = _load(args)
            result = _run_policy(trace, args)
            if args.format == "json":
                print(render_json(result.as_dict()))
            else:
                print(render_trace(trace))
                print()
                print(render_result(result))
            return 0

        if args.command == "scenarios":
            rows = []
            for name in SCENARIOS:
                trace = build_trace(name=name, packets=args.packets, seed=args.seed,
                                    **SCENARIOS[name])
                for result in compare(trace):
                    rows.append(result.as_dict())
            if args.format == "json":
                print(render_json(rows))
            else:
                print(render_scenarios(rows))
            return 0

        if args.command == "frontier":
            trace = _load(args)
            if args.step <= 0:
                raise BufferError("step must be positive")
            rows = []
            delay = 0.0
            while delay <= args.max_ms:
                result = fixed_delay(trace, delay)
                rows.append(
                    {
                        "delay_ms": delay,
                        "underruns": result.underruns,
                        "p95_ms": round(result.percentile_latency(95), 2),
                    }
                )
                delay += args.step
            if args.format == "json":
                print(render_json(rows))
            else:
                print(render_trace(trace))
                print()
                print(render_frontier(rows))
            return 0

        if args.command == "required":
            trace = _load(args)
            delay = latency_for_zero_underruns(trace, limit_ms=args.max_ms)
            if delay is None:
                print(f"no fixed delay up to {args.max_ms:g} ms plays every surviving packet")
                return 1
            print(f"{trace.name}: {delay:g} ms of fixed buffer is enough "
                  f"({trace.loss_rate * 100:.2f}% of packets are lost and cannot be recovered)")
            return 0

        if args.command == "save":
            trace = _load(args)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(trace.as_dict(), indent=2), encoding="utf-8")
            print(f"wrote {args.out} ({len(trace.packets)} packets)")
            return 0
    except (TraceError, BufferError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
