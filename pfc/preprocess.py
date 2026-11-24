"""Explicit, replaceable filesystem and cpp boundary for the frontend."""

import stat
import subprocess
from pathlib import Path

from pfc.config import DEFAULT_CPP, MAX_PREPROCESSED_BYTES, MAX_SOURCE_BYTES, PROCESS_TIMEOUT_SECONDS
from pfc.diagnostics import FrontendError, SourceLocation
from pfc.environment import compiler_environment


def _header_directories() -> tuple[Path, Path]:
    package = Path(__file__).resolve().parent
    bundled = package / "_resources"
    if bundled.is_dir():
        return bundled / "include", bundled / "fake_libc"
    # Editable checkout: use the canonical files, not a second maintained copy.
    return package.parent / "include", package.parent / "tools" / "fake_libc"


def _read_source(path: Path) -> str:
    location = SourceLocation(str(path))
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise FrontendError("source-read", "expected a regular C source file", location)
        with path.open("rb") as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
    except OSError:
        raise FrontendError("source-read", "cannot read source; check its path and permissions", location) from None
    if len(data) > MAX_SOURCE_BYTES:
        raise FrontendError("source-size", f"source exceeds {MAX_SOURCE_BYTES} bytes", location)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FrontendError("source-encoding", "source must be UTF-8", location) from None
    if "\x00" in text:
        raise FrontendError("source-encoding", "source contains a NUL byte", location)
    if not text.strip():
        raise FrontendError("source-empty", "source is empty", location)
    return text


def preprocess(source: Path, *, cpp: str = DEFAULT_CPP) -> str:
    """Read a bounded UTF-8 source and preprocess it with locations and SPEC_KEEP.

    The output limit is checked after capture, not an OS-level memory limit.
    Local includes are normal cpp reads; this function is not a filesystem sandbox.
    """
    try:
        source = source.resolve()
    except (OSError, RuntimeError, ValueError):
        raise FrontendError("source-read", "cannot resolve source path", SourceLocation(str(source))) from None
    location = SourceLocation(str(source))
    if not cpp.strip() or "\x00" in cpp:
        raise FrontendError("preprocess-config", "set a nonempty cpp executable via --cpp or PFC_CPP", location)
    if any(ord(character) < 32 for character in str(source)):
        raise FrontendError("source-path", "source path contains control characters", location)
    text = _read_source(source)
    include, fake_libc = _header_directories()
    if not (include / "spec.h").is_file() or not (fake_libc / "stdbool.h").is_file():
        raise FrontendError("preprocess-resources", "missing frontend headers; reinstall pfc", location)
    arguments = [
        cpp,
        "-E",
        "-x",
        "c",
        "-std=c11",
        "-undef",
        "-nostdinc",
        "-Werror",
        "-DSPEC_KEEP",
        "-I",
        str(include),
        "-I",
        str(fake_libc),
        "-iquote",
        str(source.parent),
        "-",
    ]
    filename = str(source).replace("\\", "\\\\").replace('"', '\\"')
    # Feed the validated bytes as text instead of letting cpp reread the main file.
    source_input = f'#line 1 "{filename}"\n{text}'
    environment = compiler_environment()
    try:
        result = subprocess.run(
            arguments,
            input=source_input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=PROCESS_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
    except FileNotFoundError:
        raise FrontendError(
            "preprocess-missing", "cpp executable not found; install cpp or set --cpp", location
        ) from None
    except subprocess.TimeoutExpired:
        raise FrontendError("preprocess-timeout", "cpp exceeded its time limit", location) from None
    except (OSError, UnicodeError):
        raise FrontendError("preprocess-error", "cannot execute cpp or decode its output", location) from None
    if result.returncode != 0:
        raise FrontendError(
            "preprocess-failed",
            "cpp failed; check includes and preprocessing directives (system headers are disabled)",
            location,
        )
    if len(result.stdout.encode("utf-8")) > MAX_PREPROCESSED_BYTES:
        raise FrontendError("preprocess-size", f"cpp output exceeds {MAX_PREPROCESSED_BYTES} bytes", location)
    if not result.stdout.strip():
        raise FrontendError("source-empty", "cpp produced no source", location)
    return result.stdout
