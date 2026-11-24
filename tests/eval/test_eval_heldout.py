"""Held-out sealing: canaries first, then hashes; verification before any run; composition checks (roadmap E12)."""

import json
import os
import shutil
from pathlib import Path

import pytest

from pfc_eval import heldout
from pfc_eval.cli import main

ROOT = Path(__file__).resolve().parents[2]
KERNEL = """#include "spec.h"

long strided_sum(const long a[], long a_len, long n, long stride)
{
    REQUIRES(0 <= n && n <= a_len && 1 <= stride && stride <= 4);
    long s = 0;
    long i = 0;
    while (i < n) {
        INVARIANT(0 <= i && i <= n + stride - 1);
        s = s + a[i];
        i = i + stride;
    }
    return s;
}
"""
B1_KERNEL = """#include "spec.h"

long count_if(const long a[], long a_len, long n)
{
    REQUIRES(0 <= n && n <= a_len);
    long c = 0;
    long i = 0;
    while (i < n) {
        INVARIANT(0 <= i && i <= n);
        if (a[i] > 0) {
            c = c + 1;
        }
        i = i + 1;
    }
    return c;
}
"""


COMPILER = os.environ.get("PFC_CLANG", "clang-19")


def _corpus(root: Path, kernels: dict[str, str], *, profiles: bool = True, drivers: bool = True) -> Path:
    """A held-out directory plus, by default, a sibling profiles directory named profiles<suffix of root>."""
    from pfc.diagnostics import FrontendError
    from pfc.lower import lower
    from pfc.parse import parse_source

    profiles_dir = root.parent / ("profiles" + root.name.removeprefix("heldout"))
    for name, text in kernels.items():
        (root / name).mkdir(parents=True)
        (root / name / "kernel.c").write_text(text, encoding="utf-8")
        if drivers:
            (root / name / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        if profiles:
            try:
                program = lower(parse_source(root / name / "kernel.c"))
            except FrontendError:
                continue  # a rejected kernel gets no profile; sealing reports the frontend problem itself
            if any(p.length is not None for p in program.functions[0].parameters):
                profiles_dir.mkdir(exist_ok=True)
                skeleton = heldout.profile_skeleton(program, name)
                (profiles_dir / f"{name}.json").write_text(json.dumps(skeleton, indent=2) + "\n", encoding="utf-8")
    return root


def test_fixed_name_list_matches_the_design():
    assert heldout.NAMES == (
        "strided_sum",
        "compact",
        "partition",
        "argmax",
        "two_sum_sorted",
        "running_max",
        "transpose",
        "insertion_sort",
        "reverse",
        "count_if",
        "max_element",
        "pairwise_diff",
        "window_sum",
    )
    assert heldout.RESERVES == {"compact": "copy_if"}


def test_canary_is_a_unique_comment_inserted_after_the_include_and_never_twice(tmp_path):
    corpus = _corpus(tmp_path / "heldout", {"strided_sum": KERNEL})
    canary = heldout.canary(seed_bytes=b"\x01" * 16)
    assert canary.startswith("pfc-heldout-") and len(canary) == len("pfc-heldout-") + 32
    text = heldout.embed(KERNEL, canary)
    assert text.splitlines()[0] == '#include "spec.h"' and text.splitlines()[1] == f"/* {canary} */"
    assert text.count(canary) == 1 and heldout.find_canary(text) == canary
    with pytest.raises(ValueError, match="already"):
        heldout.embed(text, canary)
    (corpus / "strided_sum" / "kernel.c").write_text(text, encoding="utf-8")
    assert heldout.find_canary(KERNEL) is None


def test_seal_embeds_canaries_then_hashes_and_refuses_a_second_sealing(tmp_path):
    corpus = _corpus(tmp_path / "heldout", {"strided_sum": KERNEL, "count_if": B1_KERNEL})
    record = heldout.seal(corpus, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=corpus.parent / "profiles")
    assert (corpus / "canaries.json").is_file() and (corpus / "HELDOUT.sha256").is_file()
    canaries = json.loads((corpus / "canaries.json").read_text())
    assert set(canaries) == {"strided_sum", "count_if"} and len(set(canaries.values())) == 2
    text = (corpus / "strided_sum" / "kernel.c").read_text()
    assert heldout.find_canary(text) == canaries["strided_sum"]
    inventory = (corpus / "HELDOUT.sha256").read_text().splitlines()
    names = {line.split("  ", 1)[1] for line in inventory}
    assert {"strided_sum/kernel.c", "strided_sum/main.c", "count_if/kernel.c", "canaries.json", "SEALING.json"} <= names
    assert "HELDOUT.sha256" not in names
    assert record["accepted"] == ["count_if", "strided_sum"] and record["untouched_statement"]
    assert record["outside_b1"] == ["strided_sum"] and record["b1_fraction_outside"] == 0.5
    assert record["missing_names"] and "argmax" in record["missing_names"]
    assert heldout.verify(corpus).ok
    with pytest.raises(FileExistsError):
        heldout.seal(corpus, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=corpus.parent / "profiles")


def test_seal_requires_frontend_acceptance_the_composition_rule_and_the_untouched_statement(tmp_path):
    corpus = _corpus(tmp_path / "heldout", {"count_if": B1_KERNEL})
    with pytest.raises(ValueError, match="untouched"):
        heldout.seal(corpus, untouched=False, cpp="cpp", compiler=COMPILER, profiles_dir=corpus.parent / "profiles")
    with pytest.raises(ValueError, match="B1"):
        heldout.seal(
            corpus, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=corpus.parent / "profiles"
        )  # every hand invariant is in the B1 family
    assert (
        not (corpus / "HELDOUT.sha256").exists()
        and heldout.find_canary((corpus / "count_if/kernel.c").read_text()) is None
    )
    broken = _corpus(tmp_path / "broken", {"strided_sum": KERNEL.replace("long i = 0;", "int *p = 0;")})
    with pytest.raises(ValueError, match="pfc ir"):
        heldout.seal(broken, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=broken.parent / "profiles")


def test_verify_detects_every_tampering(tmp_path):
    corpus = _corpus(tmp_path / "heldout", {"strided_sum": KERNEL, "count_if": B1_KERNEL})
    heldout.seal(corpus, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=corpus.parent / "profiles")
    assert heldout.verify(corpus).ok
    edited = corpus / "count_if" / "kernel.c"
    original = edited.read_text()
    edited.write_text(original.replace("c = c + 1", "c = c + 2"))
    result = heldout.verify(corpus)
    assert not result.ok and "count_if/kernel.c" in result.problems[0]
    edited.write_text(original)
    assert heldout.verify(corpus).ok
    (corpus / "extra" / "kernel.c").parent.mkdir()
    (corpus / "extra" / "kernel.c").write_text(KERNEL)
    assert not heldout.verify(corpus).ok  # a file absent from the inventory is tampering too
    shutil.rmtree(corpus / "extra")
    canaries = json.loads((corpus / "canaries.json").read_text())
    canaries["count_if"] = "pfc-heldout-" + "0" * 32
    (corpus / "canaries.json").write_text(json.dumps(canaries))
    assert not heldout.verify(corpus).ok
    assert not heldout.verify(tmp_path / "nowhere").ok


def test_sealed_root_detection_guards_every_campaign(tmp_path):
    corpus = _corpus(tmp_path / "heldout", {"strided_sum": KERNEL, "count_if": B1_KERNEL})
    assert heldout.sealed_root(corpus / "strided_sum") is None
    heldout.seal(corpus, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=corpus.parent / "profiles")
    assert heldout.sealed_root(corpus / "strided_sum") == corpus and heldout.sealed_root(corpus) == corpus
    assert heldout.sealed_root(ROOT / "kernels") is None
    heldout.require_intact((corpus / "count_if", ROOT / "kernels"))
    (corpus / "count_if" / "kernel.c").write_text(B1_KERNEL)
    with pytest.raises(ValueError, match="held-out"):
        heldout.require_intact((corpus / "count_if",))


def test_cli_seal_and_verify_exit_codes(tmp_path, capsys):
    corpus = _corpus(tmp_path / "heldout", {"strided_sum": KERNEL, "count_if": B1_KERNEL})
    assert main(["heldout", "verify", str(corpus)]) == 1  # not sealed yet
    assert main(["heldout", "seal", str(corpus)]) == 1  # needs the explicit untouched statement
    assert main(["heldout", "seal", str(corpus), "--untouched"]) == 1  # profiles live elsewhere: refused, not guessed
    profiles = str(corpus.parent / "profiles")
    assert main(["heldout", "seal", str(corpus), "--untouched", "--profiles", profiles, "--clang", COMPILER]) == 0
    out = capsys.readouterr().out
    assert "2 kernel(s) sealed" in out and "outside the B1 family: 1/2" in out
    assert main(["heldout", "verify", str(corpus)]) == 0
    (corpus / "count_if" / "kernel.c").write_text(B1_KERNEL)
    assert main(["heldout", "verify", str(corpus)]) == 1


ARRAY_FREE = """#include "spec.h"

long clamp_sum(long n, long k)
{
    REQUIRES(0 <= n && n <= 100 && 0 <= k && k <= 100);
    long i = 0;
    long s = 0;
    while (i < n) {
        INVARIANT(0 <= i && i <= n && s == i * k);
        s = s + k;
        i = i + 1;
    }
    return s;
}
"""


def test_profile_skeleton_binds_to_the_program_and_the_canary_does_not_change_its_digest(tmp_path):
    from pfc.lower import lower
    from pfc.parse import parse_source
    from pfc_eval import generate

    path = tmp_path / "strided_sum" / "kernel.c"
    path.parent.mkdir()
    path.write_text(KERNEL, encoding="utf-8")
    program = lower(parse_source(path))
    skeleton = heldout.profile_skeleton(program, "strided_sum")
    out = tmp_path / "profiles" / "strided_sum.json"
    out.parent.mkdir()
    out.write_text(json.dumps(skeleton, indent=2) + "\n", encoding="utf-8")
    profile = generate.load_profile(out)
    assert generate.bind(profile, program).name == "strided_sum"
    assert set(profile.arrays) == {"a"} and set(profile.scalars) == {"n", "stride"} and profile.stress == 0.1
    path.write_text(heldout.embed(KERNEL, heldout.canary()), encoding="utf-8")
    assert generate.program_digest(lower(parse_source(path))) == skeleton["program_sha256"]  # comments vanish in cpp


def test_seal_records_guard_sites_from_the_checked_emission_and_requires_half_of_the_kernels_to_have_one(tmp_path):
    corpus = _corpus(tmp_path / "heldout", {"strided_sum": KERNEL, "count_if": B1_KERNEL})
    record = heldout.seal(corpus, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=tmp_path / "profiles")
    assert record["guard_sites"]["strided_sum"] == {
        "bounds": 1,
        "division": 0,
        "sites": record["guard_sites"]["strided_sum"]["sites"],
    }
    assert len(record["guard_sites"]["strided_sum"]["sites"]) == 1 and record["guard_rule_fraction"] == 1.0
    assert record["profiles"]["strided_sum"] and record["drivers"] == ["count_if", "strided_sum"]
    scalar_only = _corpus(
        tmp_path / "scalar", {"clamp_sum": ARRAY_FREE, "clamp_sum2": ARRAY_FREE.replace("clamp_sum", "clamp_sum2")}
    )
    with pytest.raises(ValueError, match="guard"):
        heldout.seal(scalar_only, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=tmp_path / "p2")


def test_seal_requires_a_bound_profile_and_a_driver_for_every_array_kernel_and_records_profile_digests(tmp_path):
    corpus = _corpus(tmp_path / "heldout", {"strided_sum": KERNEL, "count_if": B1_KERNEL}, profiles=False)
    with pytest.raises(ValueError, match="profile"):
        heldout.seal(corpus, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=tmp_path / "profiles")
    assert not (corpus / "HELDOUT.sha256").exists()
    corpus2 = _corpus(tmp_path / "heldout2", {"strided_sum": KERNEL, "count_if": B1_KERNEL}, drivers=False)
    with pytest.raises(ValueError, match="main.c"):
        heldout.seal(corpus2, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=tmp_path / "profiles2")
    corpus3 = _corpus(tmp_path / "heldout3", {"strided_sum": KERNEL, "count_if": B1_KERNEL})
    record = heldout.seal(corpus3, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=tmp_path / "profiles3")
    assert all(len(d) == 64 for d in record["profiles"].values()) and record["profiles_dir"].endswith("profiles3")
    assert heldout.verify(corpus3).ok
    (tmp_path / "profiles3" / "count_if.json").write_text("{}", encoding="utf-8")
    verification = heldout.verify(corpus3)
    assert not verification.ok and any("count_if.json" in p for p in verification.problems)


def test_cli_skeleton_prints_or_writes_profiles_for_array_kernels_only(tmp_path, capsys):
    corpus = _corpus(tmp_path / "heldout", {"strided_sum": KERNEL, "clamp_sum": ARRAY_FREE}, profiles=False)
    assert main(["heldout", "skeleton", str(corpus)]) == 0
    out = capsys.readouterr().out
    printed = json.loads(out[out.index("{") :])
    assert printed["kernel"] == "strided_sum" and printed["profile"] == "pfc-eval-inputs-v1"
    assert "clamp_sum" not in out  # scalar kernels need no profile
    target = tmp_path / "written"
    assert main(["heldout", "skeleton", str(corpus), "--profiles", str(target)]) == 0
    assert (target / "strided_sum.json").is_file() and not (target / "clamp_sum.json").exists()
    assert main(["heldout", "skeleton", str(corpus), "--profiles", str(target)]) == 1  # never overwrites
    assert not (corpus / "HELDOUT.sha256").exists() and not (corpus / "canaries.json").exists()


def test_seal_can_report_the_composition_rule_instead_of_enforcing_it_and_records_prior_checks(tmp_path):
    # AMENDMENTS 34: a mechanically selected external set cannot be tuned to the composition rule without biasing the
    # selection; the fraction is reported, and the deterministic checks that preceded sealing are named.
    corpus = _corpus(tmp_path / "heldout", {"count_if": B1_KERNEL})
    record = heldout.seal(
        corpus,
        untouched=True,
        cpp="cpp",
        compiler=COMPILER,
        profiles_dir=corpus.parent / "profiles",
        composition="reported",
        prior_checks=("frontend acceptance", "B0 admission by the frozen admit()"),
    )
    assert record["composition_rule"] == "reported" and record["b1_fraction_outside"] == 0.0
    assert record["prior_checks"] == ["frontend acceptance", "B0 admission by the frozen admit()"]
    assert "prior_checks" in record["untouched_statement"]
    assert (corpus / "HELDOUT.sha256").is_file()
    other = _corpus(tmp_path / "other", {"count_if": B1_KERNEL})
    with pytest.raises(ValueError, match="B1"):
        heldout.seal(other, untouched=True, cpp="cpp", compiler=COMPILER, profiles_dir=other.parent / "profiles")


def test_cli_seal_accepts_composition_and_prior_check_flags(tmp_path, capsys):
    corpus = _corpus(tmp_path / "heldout", {"count_if": B1_KERNEL})
    code = main(
        [
            "heldout",
            "seal",
            str(corpus),
            "--untouched",
            "--clang",
            COMPILER,
            "--profiles",
            str(corpus.parent / "profiles"),
            "--composition",
            "reported",
            "--prior-check",
            "frontend acceptance",
            "--prior-check",
            "B0 admission",
        ]
    )
    assert code == 0 and "reported" in capsys.readouterr().out
    record = json.loads((corpus / "SEALING.json").read_text())
    assert record["composition_rule"] == "reported" and record["prior_checks"] == [
        "frontend acceptance",
        "B0 admission",
    ]
