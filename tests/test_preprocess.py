import subprocess
from pathlib import Path

import pytest

from pfc.config import MAX_PREPROCESSED_BYTES, MAX_SOURCE_BYTES
from pfc.diagnostics import FrontendError
from pfc.preprocess import preprocess


@pytest.mark.parametrize(
    ("data", "rule"),
    [
        (b"", "source-empty"),
        (b"\xff", "source-encoding"),
        (b"x" * (MAX_SOURCE_BYTES + 1), "source-size"),
        (b"\x00", "source-encoding"),
    ],
)
def test_bad_source_rejected_before_starting_cpp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes, rule: str
) -> None:
    path = tmp_path / "input.c"
    path.write_bytes(data)

    def unexpected(*args, **kwargs):
        pytest.fail("cpp must not run for invalid input")

    monkeypatch.setattr("pfc.preprocess.subprocess.run", unexpected)
    with pytest.raises(FrontendError) as failure:
        preprocess(path)
    assert failure.value.diagnostic.rule == rule


def test_missing_and_non_file_sources(tmp_path: Path) -> None:
    for path in [tmp_path / "missing.c", tmp_path]:
        with pytest.raises(FrontendError) as failure:
            preprocess(path)
        assert failure.value.diagnostic.rule == "source-read"


def test_cpp_boundary_is_explicit_and_preserves_line_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "input with spaces.c"
    path.write_text("long f(void) { return 1; }")

    def run(arguments, **kwargs):
        assert arguments[0] == "cpp with spaces"
        assert "-DSPEC_KEEP" in arguments and "-nostdinc" in arguments
        assert "-P" not in arguments and "-I" in arguments
        assert kwargs["timeout"] > 0
        assert not kwargs.get("shell", False)
        assert "return 1" in kwargs["input"]
        return subprocess.CompletedProcess(arguments, 0, stdout="long f(void) { return 1; }", stderr="")

    monkeypatch.setattr("pfc.preprocess.subprocess.run", run)
    assert "return 1" in preprocess(path, cpp="cpp with spaces")


@pytest.mark.parametrize(
    ("outcome", "rule"),
    [
        (FileNotFoundError(), "preprocess-missing"),
        (subprocess.TimeoutExpired("cpp", 10), "preprocess-timeout"),
        (PermissionError(), "preprocess-error"),
        (subprocess.CompletedProcess([], 1, stdout="", stderr="private source line"), "preprocess-failed"),
        (subprocess.CompletedProcess([], 0, stdout="x" * (MAX_PREPROCESSED_BYTES + 1), stderr=""), "preprocess-size"),
        (subprocess.CompletedProcess([], 0, stdout="", stderr=""), "source-empty"),
    ],
)
def test_cpp_failures_are_distinct_and_do_not_echo_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: object, rule: str
) -> None:
    path = tmp_path / "input.c"
    path.write_text("long f(void) { return 1; }")

    def run(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("pfc.preprocess.subprocess.run", run)
    with pytest.raises(FrontendError) as failure:
        preprocess(path)
    assert failure.value.diagnostic.rule == rule
    assert "private source line" not in str(failure.value)


def test_empty_cpp_configuration(tmp_path: Path) -> None:
    path = tmp_path / "input.c"
    path.write_text("long f(void) { return 1; }")
    with pytest.raises(FrontendError) as failure:
        preprocess(path, cpp=" ")
    assert failure.value.diagnostic.rule == "preprocess-config"


def test_missing_packaged_headers_are_not_silently_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "input.c"
    path.write_text("long f(void) { return 1; }")
    monkeypatch.setattr("pfc.preprocess._header_directories", lambda: (tmp_path, tmp_path))
    with pytest.raises(FrontendError) as failure:
        preprocess(path)
    assert failure.value.diagnostic.rule == "preprocess-resources"


def test_symlink_loop_is_a_source_error(tmp_path: Path) -> None:
    path = tmp_path / "loop.c"
    path.symlink_to(path)
    with pytest.raises(FrontendError) as failure:
        preprocess(path)
    assert failure.value.diagnostic.rule == "source-read"


def test_cpp_environment_does_not_write_dependency_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "input.c"
    path.write_text("long f(void) { return 1; }")
    monkeypatch.setenv("DEPENDENCIES_OUTPUT", str(tmp_path / "unexpected.d"))

    def run(arguments, **kwargs):
        assert "DEPENDENCIES_OUTPUT" not in kwargs["env"]
        assert "-Werror" in arguments
        return subprocess.CompletedProcess(arguments, 0, stdout="long f(void) { return 1; }", stderr="")

    monkeypatch.setattr("pfc.preprocess.subprocess.run", run)
    preprocess(path)
