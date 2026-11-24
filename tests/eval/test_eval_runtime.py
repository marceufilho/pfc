"""Runtime harness: interleaved schedule, governor label, counter tools, log-ratio bootstrap, rows (roadmap E11)."""

import pytest

from pfc_eval import runtime


def test_variants_are_the_three_modes_and_three_ablations():
    labels = [v.label for v in runtime.VARIANTS]
    assert labels == ["plain", "checked", "verified", "without-delete", "without-restrict", "without-assume"]
    assert runtime.VARIANTS[3].mode == "verified" and "delete" not in runtime.VARIANTS[3].facts
    assert runtime.BLOCKS == 3 and runtime.RUNS == 10 and runtime.WARMUPS == 3
    assert runtime.ELEMENTS == 1 << 24 and runtime.REPETITIONS == 50


def test_schedule_interleaves_variants_inside_every_run_of_every_block():
    steps = runtime.schedule(("sum", "dot"), ("plain", "checked", "verified"), blocks=2, runs=2)
    assert len(steps) == 2 * 2 * 2 * 3
    first_cycle = steps[:3]
    assert [(s.block, s.run, s.kernel, s.variant) for s in first_cycle] == [
        (1, 1, "sum", "plain"),
        (1, 1, "sum", "checked"),
        (1, 1, "sum", "verified"),
    ]
    # A B C, A B C: no variant repeats before the other variants of the same kernel ran
    for index in range(0, len(steps), 3):
        assert {s.variant for s in steps[index : index + 3]} == {"plain", "checked", "verified"}
        assert len({(s.block, s.run, s.kernel) for s in steps[index : index + 3]}) == 1
    assert steps[-1].block == 2 and steps[-1].run == 2


def test_governor_label_marks_anything_but_performance_exploratory():
    assert runtime.label("performance", "perf") == "controlled"
    assert runtime.label("performance", "cachegrind") == "controlled"
    assert runtime.label("performance", "unavailable") == "exploratory"  # no instruction count = no second measure
    assert runtime.label("powersave", "perf") == "exploratory" and runtime.label("unavailable", "perf") == "exploratory"


def test_cpu_pinning_wraps_the_program_in_taskset_and_is_recorded(tmp_path):
    assert runtime.pinned(["./program", "16", "2"], cpu=None) == ["./program", "16", "2"]
    assert runtime.pinned(["./program", "16", "2"], cpu=3) == ["taskset", "-c", "3", "./program", "16", "2"]
    with pytest.raises(ValueError, match="taskset"):
        runtime.pinned(["./program"], cpu=3, which=lambda name: None)
    assert runtime.governor(lambda path: "schedutil\n") == "schedutil"
    assert runtime.governor(lambda path: (_ for _ in ()).throw(OSError())) == "unavailable"


def test_counter_tool_prefers_perf_then_cachegrind_then_records_unavailable():
    assert runtime.counter_tool(which=lambda name: "/usr/bin/" + name) == "perf"
    assert runtime.counter_tool(which=lambda name: "/usr/bin/valgrind" if name == "valgrind" else None) == "cachegrind"
    assert runtime.counter_tool(which=lambda name: None) == "unavailable"
    perf = runtime.counter_command("perf", ["/tmp/program", "16", "2"])
    assert perf[:4] == ["perf", "stat", "-x,", "-e"] and perf[-3:] == ["/tmp/program", "16", "2"]
    cachegrind = runtime.counter_command("cachegrind", ["/tmp/program", "16", "2"])
    assert cachegrind[:2] == ["valgrind", "--tool=cachegrind"] and "--cachegrind-out-file=/dev/null" in cachegrind
    with pytest.raises(ValueError):
        runtime.counter_command("unavailable", ["/tmp/program"])


def test_counter_output_parsers_read_perf_csv_and_cachegrind_summary():
    assert runtime.parse_perf("1234567,,instructions,1000,100.00,,\n") == 1234567
    assert runtime.parse_perf("<not supported>,,instructions,0,100.00,,\n") is None
    assert runtime.parse_cachegrind("==12== I   refs:      7,654,321\n==12== I1  misses: 1\n") == 7654321
    assert runtime.parse_cachegrind("no summary here") is None


def test_log_ratio_bootstrap_is_paired_and_reports_the_geometric_ratio():
    numerators = {(1, 1): 2.0, (1, 2): 2.2, (2, 1): 1.8, (2, 2): 2.0}
    denominators = {(1, 1): 1.0, (1, 2): 1.1, (2, 1): 0.9, (2, 2): 1.0}
    ratio = runtime.ratio(numerators, denominators, seed=0)
    assert ratio["estimate"] == pytest.approx(2.0) and ratio["low"] <= 2.0 <= ratio["high"]
    assert ratio["pairs"] == 4 and ratio["replicates"] == 2000
    with pytest.raises(ValueError):
        runtime.ratio({(1, 1): 1.0}, {(1, 2): 1.0}, seed=0)  # unpaired blocks and runs are a protocol error


def test_summary_reports_absolute_medians_beside_every_ratio():
    rows = []
    for block in (1, 2):
        for run in (1, 2):
            for variant, wall in (("plain", 10.0 + run), ("checked", 20.0 + run), ("verified", 11.0 + run)):
                rows.append(
                    {
                        "kernel": "sum",
                        "variant": variant,
                        "mode": "verified" if variant == "verified" else variant,
                        "facts": "delete,restrict,assume",
                        "block": block,
                        "run": run,
                        "warmup": False,
                        "wall_ms": wall,
                        "exit_code": 0,
                        "started": f"2026-11-05T00:0{block}:{run:02d}+00:00",
                        "instructions": None,
                        "counter_tool": "unavailable",
                        "vectorized": variant != "checked",
                        "elements": 4096,
                        "repetitions": 2,
                    }
                )
    rows.append({**rows[0], "warmup": True, "block": 0, "run": 1, "wall_ms": 99.0})
    summary = runtime.summarize(rows, seed=0, governor="powersave", cpu=None)
    kernel = summary["kernels"]["sum"]
    assert kernel["medians_ms"]["plain"] == pytest.approx(11.5) and kernel["medians_ms"]["checked"] == pytest.approx(
        21.5
    )
    assert kernel["ratios"]["checked/plain"]["estimate"] > 1.7 and kernel["ratios"]["verified/plain"]["estimate"] < 1.2
    assert "verified/checked" in kernel["ratios"] and kernel["vectorized"] == {
        "plain": True,
        "checked": False,
        "verified": True,
    }
    assert summary["governor"] == "powersave" and summary["label"] == "exploratory" and summary["cpu"] is None
    assert summary["counter_tool"] == "unavailable" and kernel["instruction_ratios"] == {}
    assert summary["timed_runs"] == 12 and summary["warmups"] == 1
    table = runtime.render_table(summary)
    assert "exploratory" in table and "ms" in table and "verified/plain" in table


def test_strip_overflow_traps_rewrites_each_trap_as_the_plain_operation():
    """E26 (AMENDMENTS 39): evaluation-only ablation; the expected lines are written by hand."""
    text = (
        "long f(long x)\n{\n"
        "    long pfc_tmp_1;\n"
        "    if (__builtin_add_overflow(x, pfc_tmp_0, &pfc_tmp_1)) __builtin_trap();\n"
        "        if (__builtin_mul_overflow(i, cols, &pfc_tmp_2)) __builtin_trap();\n"
        "    if (__builtin_sub_overflow(0L, x, &pfc_tmp_3)) __builtin_trap();\n"
        "    if (!(0 <= i && i < a_len)) __builtin_trap();\n"
        "    return pfc_tmp_1;\n}\n"
    )
    stripped = runtime.strip_overflow_traps(text)
    assert stripped == (
        "long f(long x)\n{\n"
        "    long pfc_tmp_1;\n"
        "    pfc_tmp_1 = x + pfc_tmp_0;\n"
        "        pfc_tmp_2 = i * cols;\n"
        "    pfc_tmp_3 = 0L - x;\n"
        "    if (!(0 <= i && i < a_len)) __builtin_trap();\n"
        "    return pfc_tmp_1;\n}\n"
    )
    assert runtime.strip_overflow_traps("long g(void)\n{\n    return 0;\n}\n") == "long g(void)\n{\n    return 0;\n}\n"


def test_strip_overflow_traps_refuses_a_trap_it_cannot_parse():
    import pytest

    with pytest.raises(ValueError):
        runtime.strip_overflow_traps("    if (__builtin_add_overflow(f(a, b), c, &t)) __builtin_trap();\n")


def test_trap_ablation_variant_is_opt_in_and_contrasted_with_verified_and_plain():
    assert runtime.TRAP_ABLATION.label == "without-overflow-traps" and runtime.TRAP_ABLATION not in runtime.VARIANTS
    assert runtime.TRAP_ABLATION.mode == "verified" and runtime.TRAP_ABLATION.transform == "strip-overflow-traps"
    rows = [
        {
            "kernel": "k",
            "variant": v,
            "block": 1,
            "run": r,
            "warmup": False,
            "wall_ms": ms,
            "exit_code": 0,
            "instructions": None,
            "counter_tool": "unavailable",
            "vectorized": False,
        }
        for v, ms in (("plain", 10.0), ("verified", 20.0), ("checked", 20.0), ("without-overflow-traps", 10.0))
        for r in (1, 2)
    ]
    ratios = runtime.summarize(rows, seed=0, governor="powersave", cpu=None)["kernels"]["k"]["ratios"]
    assert ratios["without-overflow-traps/verified"]["estimate"] == pytest_approx(0.5)
    assert ratios["without-overflow-traps/plain"]["estimate"] == pytest_approx(1.0)


def pytest_approx(value):
    import pytest

    return pytest.approx(value)


@pytest.mark.integration
@pytest.mark.parametrize("kernel", ["prefix_sum", "matvec", "sum"])
def test_trap_ablation_prints_the_same_checksum_as_plain(kernel, tmp_path):
    """The rewritten verified C computes what plain computes on the benchmark driver (small defined input)."""
    import subprocess
    from pathlib import Path

    from pfc import smt
    from pfc.config import BENCH_FLAGS
    from pfc.emit import emit
    from pfc.facts import ALL_FACTS
    from pfc.lower import lower
    from pfc.parse import parse_source
    from pfc.target import read_target

    root = Path(__file__).resolve().parents[2]
    source = root / "kernels" / kernel / "kernel.c"
    program, target = lower(parse_source(source)), read_target("clang-19")
    verified = emit(program, target, mode="verified", proof=smt.check(program, target), facts=ALL_FACTS)
    texts = {"plain": emit(program, target, mode="plain").c, "ablation": runtime.strip_overflow_traps(verified.c)}
    assert "_overflow(" not in "\n".join(
        line for line in texts["ablation"].splitlines() if not line.lstrip().startswith("//")
    )
    printed = {}
    for name, text in texts.items():
        directory = tmp_path / name
        directory.mkdir()
        (directory / "kernel.c").write_text(text, encoding="utf-8")
        command = ["clang-19", *BENCH_FLAGS, str(directory / "kernel.c"), str(source.parent / "main.c")]
        subprocess.run([*command, "-o", str(directory / "p")], check=True, capture_output=True, timeout=120)
        printed[name] = subprocess.run([str(directory / "p"), "64", "2"], capture_output=True, text=True, timeout=60)
    assert printed["plain"].returncode == 0 and printed["ablation"].returncode == 0
    assert printed["plain"].stdout == printed["ablation"].stdout and printed["plain"].stdout.strip()
