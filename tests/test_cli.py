"""Command line behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jitter_buffer_lab.cli import build_parser, main
from jitter_buffer_lab.trace import build_trace


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_version_flag_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "jitter-lab" in capsys.readouterr().out


def test_trace_command_reports_the_link(capsys):
    assert main(["trace", "--scenario", "wifi", "--packets", "100"]) == 0
    out = capsys.readouterr().out
    assert "Trace: wifi" in out
    assert "100 (2.0 s of audio at 20 ms)" in out


def test_trace_command_json(capsys):
    assert main(["trace", "--scenario", "lan", "--packets", "50", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "lan"
    assert payload["packets"] == 50


def test_compare_command_prints_a_table(capsys):
    assert main(["compare", "--scenario", "wifi", "--packets", "200"]) == 0
    out = capsys.readouterr().out
    assert "Buffer policies on trace 'wifi'" in out
    assert "fixed 20ms" in out
    assert "adaptive" in out


def test_compare_command_json_is_a_list_of_results(capsys):
    assert main(["compare", "--scenario", "spiky", "--packets", "200", "--format", "json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 5
    assert rows[0]["policy"] == "fixed 0ms"
    assert rows[0]["underruns"] == 200


@pytest.mark.parametrize(
    "policy, fragment",
    [
        ("adaptive", "adaptive"),
        ("measured-p99", "measured p99"),
        ("fixed-50", "fixed 50ms"),
        ("clock-only", "fixed 0ms"),
        ("passthrough", "passthrough"),
    ],
)
def test_run_command_covers_every_policy(capsys, policy, fragment):
    assert main(["run", "--scenario", "wifi", "--packets", "120", "--policy", policy]) == 0
    assert f"Policy: {fragment}" in capsys.readouterr().out


def test_run_command_accepts_a_custom_fixed_delay(capsys):
    assert main(["run", "--packets", "60", "--policy", "fixed-20", "--delay-ms", "37.5"]) == 0
    assert "Policy: fixed 37.5ms" in capsys.readouterr().out


def test_run_command_json(capsys):
    assert main(["run", "--packets", "60", "--policy", "adaptive", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["policy"].startswith("adaptive")
    assert payload["packets"] == 60


def test_scenarios_command_covers_the_cross_product(capsys):
    assert main(["scenarios", "--packets", "80"]) == 0
    out = capsys.readouterr().out
    for name in ("lan", "wifi", "congested", "spiky"):
        assert name in out


def test_scenarios_command_json(capsys):
    assert main(["scenarios", "--packets", "80", "--format", "json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 20
    assert {row["trace"] for row in rows} == {"lan", "wifi", "congested", "spiky"}


def test_frontier_command_walks_the_curve(capsys):
    assert main(["frontier", "--scenario", "spiky", "--packets", "200", "--step", "20",
                 "--max-ms", "60"]) == 0
    out = capsys.readouterr().out
    assert "Latency against continuity for a fixed buffer" in out
    assert out.count("\n") > 8


def test_frontier_command_json(capsys):
    assert main(["frontier", "--packets", "120", "--step", "30", "--max-ms", "60",
                 "--format", "json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["delay_ms"] for row in rows] == [0.0, 30.0, 60.0]


def test_frontier_rejects_a_zero_step(capsys):
    assert main(["frontier", "--packets", "60", "--step", "0"]) == 2
    assert "step must be positive" in capsys.readouterr().err


def test_required_command_reports_the_smallest_buffer(capsys):
    assert main(["required", "--scenario", "lan", "--packets", "300"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("lan: ")
    assert "of fixed buffer is enough" in out


def test_required_command_fails_when_the_limit_is_too_low(capsys):
    assert main(["required", "--scenario", "spiky", "--packets", "300", "--max-ms", "5"]) == 1
    assert "no fixed delay up to 5 ms" in capsys.readouterr().out


def test_save_then_replay_a_trace(tmp_path: Path, capsys):
    target = tmp_path / "nested" / "capture.json"
    assert main(["save", "--scenario", "wifi", "--packets", "120", "--out", str(target)]) == 0
    assert target.exists()
    assert "wrote " in capsys.readouterr().out

    assert main(["compare", "--trace-file", str(target)]) == 0
    out = capsys.readouterr().out
    assert "Buffer policies on trace 'wifi'" in out


def test_trace_file_overrides_the_scenario(tmp_path: Path, capsys):
    saved = tmp_path / "capture.json"
    saved.write_text(
        json.dumps(
            {
                "name": "handmade",
                "interval_ms": 20.0,
                "packets": [
                    {"sent_ms": 0.0, "arrival_ms": 4.0},
                    {"sent_ms": 20.0, "arrival_ms": 26.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert main(["trace", "--trace-file", str(saved)]) == 0
    assert "Trace: handmade" in capsys.readouterr().out


def test_missing_trace_file_is_a_clean_error(tmp_path: Path, capsys):
    assert main(["trace", "--trace-file", str(tmp_path / "gone.json")]) == 2
    assert "file not found" in capsys.readouterr().err


def test_unknown_scenario_is_rejected_by_argparse():
    with pytest.raises(SystemExit):
        main(["compare", "--scenario", "satellite"])


def test_generated_trace_matches_the_saved_one(tmp_path: Path):
    target = tmp_path / "t.json"
    main(["save", "--scenario", "congested", "--packets", "40", "--out", str(target)])
    expected = build_trace(name="congested", packets=40, seed=7, jitter_ms=14.0, loss=0.02,
                           base_delay_ms=15.0)
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["name"] == expected.name
    assert len(saved["packets"]) == len(expected.packets)
