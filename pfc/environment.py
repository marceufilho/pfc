"""Read toolchain metadata at explicit process and filesystem boundaries."""

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import z3

from pfc.config import C_FLAGS, PROCESS_TIMEOUT_SECONDS


class ToolchainError(RuntimeError):
    """An external compiler could not provide usable metadata."""


@dataclass(frozen=True)
class Environment:
    """Observed versions and configured smoke-test flags, not benchmark data."""

    python: str
    z3: str
    clang: str
    clang_target: str
    cpu: str
    system: str
    c_flags: tuple[str, ...]


def compiler_environment() -> dict[str, str]:
    """Remove implicit include paths and dependency-file output directives."""
    environment = os.environ.copy()
    for name in (
        "CPATH",
        "C_INCLUDE_PATH",
        "CPLUS_INCLUDE_PATH",
        "OBJC_INCLUDE_PATH",
        "DEPENDENCIES_OUTPUT",
        "SUNPRO_DEPENDENCIES",
    ):
        environment.pop(name, None)
    return environment


def resolve_compiler(compiler: str) -> str:
    """Resolve the selected compiler once so every subprocess records the same executable path."""
    return str(Path(shutil.which(compiler) or compiler).resolve())


def compiler_output(compiler: str, *arguments: str, source: str | None = None) -> str:
    """Query clang using an argument list and optional fixed stdin source."""
    if not compiler.strip():
        raise ToolchainError("clang executable cannot be empty; set --clang or PFC_CLANG")
    if "\x00" in compiler:
        raise ToolchainError("clang executable cannot contain NUL; set --clang or PFC_CLANG")
    try:
        result = subprocess.run(
            [compiler, *arguments],
            input=source,
            capture_output=True,
            text=True,
            check=True,
            timeout=PROCESS_TIMEOUT_SECONDS,
            env=compiler_environment(),
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise ToolchainError(
            f"cannot query clang ({type(error).__name__}); install clang or set --clang / PFC_CLANG"
        ) from error
    output = result.stdout.strip()
    if not output:
        raise ToolchainError("clang returned empty metadata")
    return output


def _cpu_model() -> str:
    """Use Linux's CPU model without collecting hostname, username or environment."""
    try:
        cpu_info = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return platform.processor() or "unavailable"
    for line in cpu_info.splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip() == "model name":
            return value.strip()
    return platform.processor() or "unavailable"


def collect_environment(compiler: str) -> Environment:
    """Query the selected compiler and installed solver without running experiments."""
    clang = compiler_output(compiler, "--version").splitlines()[0]
    if "clang version" not in clang.lower():
        raise ToolchainError("selected executable does not identify itself as clang")
    return Environment(
        python=platform.python_version(),
        z3=str(z3.get_version_string()),
        clang=clang,
        clang_target=compiler_output(compiler, "-dumpmachine"),
        cpu=_cpu_model(),
        system=f"{platform.system()} {platform.release()} {platform.machine()}",
        c_flags=C_FLAGS,
    )


def render_markdown(environment: Environment) -> str:
    """Render observed metadata deterministically (no timestamp or private paths)."""
    values = (
        ("Python", environment.python),
        ("Z3", environment.z3),
        ("clang", environment.clang),
        ("clang target", environment.clang_target),
        ("CPU", environment.cpu),
        ("System", environment.system),
        ("C smoke-test flags", " ".join(environment.c_flags)),
    )
    rows = [
        "# Environment",
        "",
        "Step-1 toolchain metadata; not benchmark results.",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]
    for label, value in values:
        safe_value = value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")
        rows.append(f"| {label} | {safe_value} |")
    rows.extend(["", "Regenerate: `make env ENV_ARGS=--force` (explicitly replaces this file).", ""])
    return "\n".join(rows)
