import subprocess

import pytest

from pfc.environment import collect_environment, render_markdown


def test_collect_environment_at_process_boundary(monkeypatch):
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        output = "clang version 19.1.1\nTarget: test\n" if "--version" in arguments else "x86_64-test\n"
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr("pfc.environment.subprocess.run", run)
    record = collect_environment("compiler with spaces")
    assert record.clang == "clang version 19.1.1"
    assert record.clang_target == "x86_64-test"
    assert record.python
    assert record.cpu
    assert record.z3
    assert calls[0][0] == ["compiler with spaces", "--version"]
    assert all(call[1]["timeout"] > 0 and call[1]["check"] for call in calls)
    assert all(not call[1].get("shell", False) for call in calls)


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError(), subprocess.TimeoutExpired("clang", 10), subprocess.CalledProcessError(1, "clang")],
)
def test_compiler_failures_are_actionable(monkeypatch, failure):
    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr("pfc.environment.subprocess.run", run)
    with pytest.raises(RuntimeError, match="clang"):
        collect_environment("clang")


@pytest.mark.parametrize("output", ["", "gcc version 13", "\n"])
def test_rejects_empty_or_wrong_compiler(monkeypatch, output):
    monkeypatch.setattr(
        "pfc.environment.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=output, stderr=""),
    )
    with pytest.raises(RuntimeError, match="clang"):
        collect_environment("clang")


def test_markdown_has_reproduction_metadata(environment_record):
    report = render_markdown(environment_record)
    for value in ["Python", "Z3", "clang", "CPU", "-std=c11", "x86_64-pc-linux-gnu"]:
        assert value in report
    assert "not benchmark results" in report
