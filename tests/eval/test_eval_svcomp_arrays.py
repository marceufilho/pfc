"""External held-out set ported from SV-COMP array-tiling / array-multidimensional by fixed rules (AMENDMENTS 34)."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from pfc.build import C_FLAGS
from pfc.emit import emit
from pfc.lower import lower
from pfc.parse import parse_source
from pfc.target import read_target
from pfc_eval import generate, interpret, ports, svcomp_arrays

ROOT = Path(__file__).resolve().parents[2]
COMPILER = os.environ.get("PFC_CLANG", "clang-19")
B0_PATTERN = re.compile(r"^\(\((\d+|\(-\d+\)|[A-Za-z_]\w*) <= [a-z]\) && \([a-z] <= .+\)\)$")  # expressions.render form


def _program(task, tmp_path):
    path = tmp_path / task.name / "kernel.c"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svcomp_arrays.render(task), encoding="utf-8")
    return lower(parse_source(path))


def test_candidates_follow_the_fixed_selection_rule():
    tasks = svcomp_arrays.TASKS
    assert len(tasks) == 46 and len({t.name for t in tasks}) == 46
    tiling = [t.upstream for t in tasks if t.family == "array-tiling"]
    multi = [t.upstream for t in tasks if t.family == "array-multidimensional"]
    assert len(tiling) == 26 and tiling == sorted(tiling) and len(multi) == 20 and multi == sorted(multi)
    assert set(svcomp_arrays.EXCLUDED) == {"array-tiling/mlceu.c", "array-tiling/mlceu2.c", "array-tiling/skippedu.c"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", t.upstream_sha256) for t in tasks)
    assert all(set(svcomp_arrays.COMMON) <= set(t.transformations) for t in tasks)
    assert all(svcomp_arrays.NAME_PATTERN.match(t.name) for t in tasks)
    assert "before any proposer" in svcomp_arrays.SELECTION and len(svcomp_arrays.RULES) == 8


@pytest.mark.parametrize("task", svcomp_arrays.TASKS, ids=lambda t: t.name)
def test_every_task_is_accepted_binds_its_profile_and_carries_mechanical_b0(task, tmp_path):
    program = _program(task, tmp_path)
    assert program.functions[0].name == task.name
    invariants = ports.hand_invariants(program)
    assert invariants and all(B0_PATTERN.match(text) for text in invariants), invariants
    data = svcomp_arrays.profile(task, program)
    profile = generate.parse_profile(data)
    assert generate.bind(profile, program).name == task.name
    assert data["boundary"] and data["stress"] == 0.1
    arguments = svcomp_arrays.driver_arguments(task)
    assert len(arguments) == len(task.parameters)
    assert svcomp_arrays.render(task) == svcomp_arrays.render(task)


@pytest.mark.parametrize("name", ["mbpr2", "nr3", "copy_2_u", "copy_partial_2_n_u", "init_4_n_u", "rewrev"])
def test_profiles_generate_accepted_cases_without_exhausting_the_budget(name, tmp_path):
    task = next(t for t in svcomp_arrays.TASKS if t.name == name)
    program = _program(task, tmp_path)
    profile = generate.parse_profile(svcomp_arrays.profile(task, program))
    small = generate.Profile(
        profile.kernel,
        profile.function,
        profile.program_sha256,
        40,
        profile.arrays,
        profile.scalars,
        profile.boundary,
        0.1,
    )
    generated = generate.generate(small, program, read_target(COMPILER), seed=3)
    assert len(generated.cases) == 40 and generated.cases[0].boundary
    assert all(isinstance(c.expected, interpret.Observation) for c in generated.cases)


@pytest.mark.integration
@pytest.mark.parametrize("name", ["pnr3", "transpose_u", "max_3_n_u"])
def test_driver_compiles_with_the_checked_emission_and_runs(name, tmp_path):
    task = next(t for t in svcomp_arrays.TASKS if t.name == name)
    program = _program(task, tmp_path)
    target = read_target(COMPILER)
    (tmp_path / "checked.c").write_text(emit(program, target, mode="checked").c, encoding="utf-8")
    (tmp_path / "main.c").write_text(svcomp_arrays.driver(task, program), encoding="utf-8")
    subprocess.run(
        [COMPILER, *C_FLAGS, str(tmp_path / "checked.c"), str(tmp_path / "main.c"), "-o", str(tmp_path / "program")],
        check=True,
    )
    result = subprocess.run([str(tmp_path / "program"), "64", "2"], capture_output=True, text=True, check=False)
    assert result.returncode == 0 and result.stdout.strip().isdigit()


@pytest.mark.integration
def test_write_records_provenance_and_skips_a_task_whose_b0_is_rejected(tmp_path):
    chosen = [t for t in svcomp_arrays.TASKS if t.name in ("mbpr2", "init_2_n_u")]
    broken = svcomp_arrays.Task(
        **{
            **chosen[0].__dict__,
            "name": "mbpr2_broken",
            "body": chosen[0].body.replace("i <= CELLCOUNT / 2 + 1", "i <= 1"),
        }
    )
    destination, profiles = tmp_path / "heldout", tmp_path / "profiles"
    provenance = svcomp_arrays.write(destination, profiles, compiler=COMPILER, tasks=[*chosen, broken])
    assert [e["name"] for e in provenance["entries"]] == ["mbpr2", "init_2_n_u"]
    assert [e["name"] for e in provenance["rejected"]] == ["mbpr2_broken"]
    assert provenance["rejected"][0]["b0_admitted"] is False and provenance["rejected"][0]["frontend_rejection"] is None
    assert not (destination / "mbpr2_broken").exists()
    for entry in provenance["entries"]:
        assert entry["b0_admitted"] is True and entry["guard_sites"] >= 1 and entry["required_obligations"] >= 1
        assert (destination / entry["name"] / "kernel.c").is_file() and (
            destination / entry["name"] / "main.c"
        ).is_file()
        bound = generate.load_profile(profiles / f"{entry['name']}.json")
        assert generate.bind(bound, lower(parse_source(destination / entry["name"] / "kernel.c"))).name == entry["name"]
    saved = json.loads((destination / "PROVENANCE.json").read_text())
    assert saved["selection"] == svcomp_arrays.SELECTION and saved["excluded"] == svcomp_arrays.EXCLUDED
    with pytest.raises(FileExistsError):
        svcomp_arrays.write(destination, profiles, compiler=COMPILER, tasks=chosen)


def test_write_records_a_port_the_frozen_compiler_rejects_instead_of_working_around_it(tmp_path, monkeypatch):
    from pfc.diagnostics import FrontendError

    chosen = [t for t in svcomp_arrays.TASKS if t.name in ("rew", "poly1")]

    def reject(program, target, *, mode):
        if program.functions[0].name == "poly1":
            raise FrontendError("vc-complexity", "VC report exceeds the cumulative formula limit", None)
        return original(program, target, mode=mode)

    original = svcomp_arrays.emit
    monkeypatch.setattr(svcomp_arrays, "emit", reject)
    provenance = svcomp_arrays.write(tmp_path / "heldout", tmp_path / "profiles", compiler=COMPILER, tasks=chosen)
    assert [e["name"] for e in provenance["entries"]] == ["rew"]
    assert provenance["rejected"][0]["name"] == "poly1"
    assert provenance["rejected"][0]["frontend_rejection"].startswith("vc-complexity")
    assert not (tmp_path / "heldout" / "poly1").exists() and not (tmp_path / "profiles" / "poly1.json").exists()
