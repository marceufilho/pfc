import json
import os

import pytest

from pfc.diagnostics import FrontendError


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["plain", "checked", "verified"])
def test_build_records_real_compiler_flags_cpu_and_objects(tmp_path, mode):
    from pfc.build import build_source

    source = tmp_path / "kernel.c"
    source.write_text(
        "long f(long a[],long a_len) { REQUIRES(a_len>0); return a[0]; }".replace("REQUIRES", "__spec_requires")
    )
    output = tmp_path / mode
    report = build_source(source, output, mode=mode, compiler=os.environ.get("PFC_CLANG", "clang-19"))
    assert report.passed and (output / "kernel.o").is_file()
    data = json.loads((output / "build.json").read_text())
    assert data["environment"]["cpu"] and data["environment"]["clang"]
    assert data["flags"] == ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"]
    assert data["compilation"]["returncode"] == 0


def test_build_preview_and_existing_destination_preserve_files(tmp_path):
    from pfc.build import build_source

    source = tmp_path / "kernel.c"
    source.write_text("long f(void){return 0;}")
    output = tmp_path / "build"
    report = build_source(source, output, mode="plain", compiler=os.environ.get("PFC_CLANG", "clang-19"), dry_run=True)
    assert report.dry_run and report.compilation is None and not output.exists()
    output.mkdir()
    sentinel = output / "keep"
    sentinel.write_text("unchanged")
    with pytest.raises(FrontendError, match="build-output"):
        build_source(source, output, mode="plain", compiler="/missing/clang")
    assert sentinel.read_text() == "unchanged"


def test_failed_compilation_and_verification_are_preserved(tmp_path):
    from pfc.build import build_source
    from pfc.difftest import ProcessResult

    source = tmp_path / "kernel.c"
    source.write_text("long f(long a[],long a_len){return a[0];}")
    output = tmp_path / "bad"
    result = build_source(
        source,
        output,
        mode="verified",
        compiler=os.environ.get("PFC_CLANG", "clang-19"),
        runner=lambda args, cwd: ProcessResult("error", "", 1, "compile failed"),
    )
    assert not result.passed and (output / "build.json").exists()
    assert "__builtin_trap" in (output / "kernel.c").read_text()
