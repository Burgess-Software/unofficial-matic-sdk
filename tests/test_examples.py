from __future__ import annotations

from pathlib import Path


def test_every_documented_example_compiles() -> None:
    examples = tuple(sorted(Path("examples").glob("*.py")))
    assert {path.name for path in examples} == {
        "create_schedule.py",
        "decode_maps.py",
        "decoded_telemetry.py",
        "dock.py",
        "joystick.py",
        "start_coverage.py",
        "stream_pose.py",
    }
    for path in examples:
        compile(path.read_bytes(), str(path), "exec")
