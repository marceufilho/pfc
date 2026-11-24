import json
import os
from pathlib import Path

import pytest

from pfc.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_benchmark_quartiles_and_invalid_samples():
    from pfc.bench import statistics

    assert statistics([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == (5.5, 4.5)
    for samples in ([], [1] * 9, [float("nan")] * 10, [-1] * 10, [True] * 10):
        with pytest.raises(ValueError):
            statistics(samples)


def test_bench_dry_run_creates_nothing(tmp_path, capsys):
    output = tmp_path / "bench"
    assert main(["bench", str(ROOT / "kernels/sum"), "--output", str(output), "--dry-run", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] and data["variants"] == 6
    assert not output.exists()


@pytest.mark.parametrize("relative_compiler", [False, True])
def test_benchmark_uses_same_flags_and_preserves_all_samples(tmp_path, monkeypatch, relative_compiler):
    import shutil

    from pfc.bench import run

    compiler = os.environ.get("PFC_CLANG", "clang-19")
    if relative_compiler:
        (tmp_path / "selected-clang").symlink_to(Path(shutil.which(compiler)).resolve())
        monkeypatch.chdir(tmp_path)
        compiler = "./selected-clang"

    calls = []

    def timing(arguments, directory):
        calls.append(arguments)
        destination = Path(arguments[arguments.index("--export-json") + 1])
        destination.write_text(json.dumps({"results": [{"times": list(range(1, 11)), "exit_codes": [0] * 10}]}))
        return None

    result = run(
        (ROOT / "kernels/sum",),
        tmp_path / "bench",
        compiler=compiler,
        hyperfine="fake",
        timer=timing,
    )
    assert len(result) == 6 and all(r.median == 5.5 and r.iqr == 4.5 for r in result)
    for arguments in calls:
        assert arguments[1:5] == ("-w", "3", "-r", "10") and "--shell=none" in arguments
    for path in (tmp_path / "bench").glob("sum/*/build.json"):
        assert json.loads(path.read_text())["flags"] == [
            "-std=c11",
            "-O3",
            "-march=native",
            "-Wall",
            "-Wextra",
            "-Werror",
        ]
    assert (tmp_path / "bench/results.csv").is_file()
    assert "remark:" in (tmp_path / "bench/sum/plain/vectorization.txt").read_text()
    from pfc.bench import freeze
    from pfc.report import load

    freeze(tmp_path / "bench", tmp_path / "frozen")
    assert load(tmp_path / "frozen").passed
    assert not list((tmp_path / "frozen").rglob("program"))
    assert not list((tmp_path / "frozen").rglob("*.o"))
    assert (tmp_path / "frozen/tables.md").read_bytes() == (tmp_path / "bench/tables.md").read_bytes()


def test_noncanonical_benchmark_directory_is_rejected_before_writes(tmp_path):
    import shutil

    from pfc.bench import run

    source = tmp_path / "renamed"
    shutil.copytree(ROOT / "kernels/sum", source)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="directory"):
        run(
            (source,),
            output,
            compiler=os.environ.get("PFC_CLANG", "clang-19"),
            hyperfine="fake",
            timer=lambda *args: pytest.fail("invalid profile directory must not reach timing"),
        )
    assert not output.exists()


def test_malformed_benchmark_identity_is_a_clean_cli_error(tmp_path, capsys):
    from pfc import report

    directory = tmp_path / "report"
    directory.mkdir()
    (directory / "benchmark.json").write_text(json.dumps([{"kernel": [], "variant": "plain", "samples": [1] * 10}]))
    report.seal(directory, report.Summary("test", (report.row("sum", None, passed=False),), False))
    assert main(["report", str(directory), "--json"]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_malformed_hyperfine_result_is_rejected_as_data(tmp_path):
    from pfc.bench import run

    def invalid(arguments, directory):
        Path(arguments[arguments.index("--export-json") + 1]).write_text('{"results":[[]]}')

    with pytest.raises(ValueError, match="hyperfine"):
        run(
            (ROOT / "kernels/sum",),
            tmp_path / "invalid-timer",
            compiler=os.environ.get("PFC_CLANG", "clang-19"),
            hyperfine="fake",
            timer=invalid,
        )
    assert not (tmp_path / "invalid-timer/manifest.json").exists()


def test_relative_hyperfine_is_resolved_before_changing_working_directory(tmp_path, monkeypatch):
    import sys

    from pfc.bench import run

    timer = tmp_path / "timer-tool"
    timer.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\nfrom pathlib import Path\n"
        "if '--version' in sys.argv:\n    print('controlled test timer')\n"
        "else:\n"
        "    output = Path(sys.argv[sys.argv.index('--export-json') + 1])\n"
        "    output.write_text(json.dumps({'results': [{'times': [1]*10, 'exit_codes': [0]*10}]}))\n"
    )
    timer.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "bench"
    result = run(
        (ROOT / "kernels/sum",), output, compiler=os.environ.get("PFC_CLANG", "clang-19"), hyperfine="./timer-tool"
    )
    assert len(result) == 6
    assert json.loads((output / "protocol.json").read_text())["hyperfine"] == "controlled test timer"
