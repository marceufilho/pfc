"""Ported Zilu loops: deterministic rendering, provenance, B0 ceiling and the obfuscated stratum (roadmap E10)."""

import hashlib
import json
from pathlib import Path

import pytest

from pfc.lower import lower
from pfc.parse import parse_source
from pfc_eval import ports

ROOT = Path(__file__).resolve().parents[2]
PORTED = ROOT / "bench/external/zilu"
OBFUSCATED = ROOT / "bench/external/zilu-obfuscated"


def test_thirty_ports_with_fixed_selection_and_documented_skips():
    assert len(ports.PORTS) == 30 and len({p.name for p in ports.PORTS}) == 30
    numbers = [int(p.name[4:]) for p in ports.PORTS]
    assert numbers == sorted(numbers) and numbers[0] == 1 and numbers[-1] == 37
    skipped_numbers = {int(name[9:11]) for name in ports.SKIPPED}
    assert skipped_numbers == {3, 7, 9, 10, 21, 28, 31}
    assert (
        set(range(1, 38)) - set(numbers) - skipped_numbers == set()
    )  # every number up to the last port is accounted for
    assert all(len(p.upstream_sha256) == 64 for p in ports.PORTS)
    assert "B0" in ports.SELECTION and ports.UPSTREAM["commit"] == "f3feef51a07ffe7c6ef58f3da3b7fe041af4fd25"


def test_render_is_deterministic_and_in_the_kernel_style():
    port = ports.PORTS[0]
    text = ports.render(port)
    assert text == ports.render(port)
    assert text.startswith('#include "spec.h"\n\nlong zilu01(long x, long y, long steps)\n{\n    REQUIRES(')
    assert "    while (step < steps) {\n        INVARIANT(x >= 1 && y >= 1);\n        x = x + y;" in text
    assert text.endswith("    ASSERT(y >= 1);\n    return y;\n}\n")
    nested = next(p for p in ports.PORTS if p.name == "zilu32")
    rendered = ports.render(nested)
    assert "        if (x == 1) {\n            x = 2;\n        } else {\n            if (x == 2) {" in rendered


@pytest.mark.parametrize(
    "invariant,expected",
    [
        ("0 <= i && i <= n", True),
        ("x == y && x >= 0", True),
        ("l > 0", True),
        ("x <= n", True),
        ("k > i - j", False),
        ("x <= y + 99", False),
        ("high == low + 2 * mid && mid >= 0", False),
        ("x == 1 || x == 2", False),
        ("x <= 10", False),
    ],
)
def test_b1_family_membership_is_syntactic(invariant, expected):
    assert ports.in_b1_family(invariant) is expected


def test_committed_ports_match_the_generator_and_their_provenance():
    provenance = json.loads((PORTED / "PROVENANCE.json").read_text(encoding="utf-8"))
    entries = {entry["name"]: entry for entry in provenance["entries"]}
    assert set(entries) == {p.name for p in ports.PORTS} and provenance["skipped"] == ports.SKIPPED
    for port in ports.PORTS:
        path = PORTED / port.name / "kernel.c"
        data = path.read_bytes()
        assert data.decode("utf-8") == ports.render(port), port.name
        entry = entries[port.name]
        assert entry["port_sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["upstream_sha256"] == port.upstream_sha256 and entry["hand_invariant"] == port.invariant
        obfuscated = (OBFUSCATED / port.name / "kernel.c").read_bytes()
        assert entry["obfuscated_sha256"] == hashlib.sha256(obfuscated).hexdigest() and obfuscated != data
        assert entry["b0_admitted"] is True and entry["obfuscated_b0_admitted"] is True, port.name
        assert entry["transformations"][:4] == list(ports.COMMON[:4])
    assert (PORTED / "LICENSE.GPLv2.txt").is_file()
    outside = sum(not entry["b1_family"] for entry in entries.values())
    assert outside >= 10  # the ported stratum is not B1-trivial as a whole


@pytest.mark.integration
def test_every_port_lowers_and_keeps_one_loop_with_a_hand_invariant():
    for port in ports.PORTS:
        program = lower(parse_source(PORTED / port.name / "kernel.c"))
        assert ports.hand_invariants(program) and program.functions[0].name == port.name
        variant = lower(parse_source(OBFUSCATED / port.name / "kernel.c"))
        assert len(ports.hand_invariants(variant)) == 1


def test_generator_refuses_an_existing_destination(tmp_path):
    (tmp_path / "zilu").mkdir()
    assert ports.main(["--output", str(tmp_path)]) == 1


COMPOSED = ROOT / "bench/external/zilu-composed"


def test_composition_pairs_every_port_once_and_renames_the_second_loop(tmp_path):
    pairs = ports.pairs(ports.PORTS, seed=0)
    assert len(pairs) == 15 and sorted(p.name for a, b in pairs for p in (a, b)) == sorted(p.name for p in ports.PORTS)
    assert pairs != ports.pairs(ports.PORTS, seed=1)
    first, second = ports.PORTS[0], ports.PORTS[1]  # zilu01 (x, y, steps) and zilu02
    composite = ports.compose(first, second, index=1)
    assert composite.name == "zilupair01" and composite.parts == ("zilu01", "zilu02")
    renamed = dict(composite.renaming)
    assert set(renamed) == set(ports.variable_names(second)) and all(v.endswith("2") for v in renamed.values())
    assert not set(renamed.values()) & set(ports.variable_names(first))
    text = ports.render_composite(composite)
    assert text.startswith('#include "spec.h"\n\nlong zilupair01(long x, long y, long steps, ')
    assert text.count("while (") == 2 and text.count("INVARIANT(") == 2 and text.count("ASSERT(") == 2
    assert text.index("ASSERT(y >= 1);") < text.rindex("while (")  # the first goal is asserted right after its loop
    path = tmp_path / "kernel.c"
    path.write_text(text, encoding="utf-8")
    program = lower(parse_source(path))
    assert len(ports.hand_invariants(program)) == 2 and f"INVARIANT({ports.rename(second.invariant, renamed)});" in text


def test_write_composed_validates_b0_and_records_provenance(tmp_path):
    provenance = ports.write_composed(tmp_path, seed=0, compiler="clang-19", pairs=ports.pairs(ports.PORTS, seed=0)[:2])
    entries = provenance["entries"]
    assert isinstance(entries, list) and len(entries) == 2
    assert {"name", "parts", "renaming", "b0_admitted", "b0_problem", "composed_sha256", "loops"} <= set(entries[0])
    written = sorted(p.name for p in (tmp_path / "zilu-composed").iterdir() if p.is_dir())
    assert written == sorted(e["name"] for e in entries if e["b0_admitted"])  # only admissible composites are tasks
    with pytest.raises(FileExistsError):
        ports.write_composed(tmp_path, seed=0, compiler="clang-19", pairs=())


def test_committed_composed_stratum_matches_the_generator():
    provenance = json.loads((COMPOSED / "PROVENANCE.json").read_text(encoding="utf-8"))
    by_name = {p.name: p for p in ports.PORTS}
    for entry in provenance["entries"]:
        a, b = (by_name[name] for name in entry["parts"])
        composite = ports.compose(a, b, index=int(entry["name"][8:]))
        text = ports.render_composite(composite)
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == entry["composed_sha256"]
        assert (COMPOSED / entry["name"] / "kernel.c").read_text(encoding="utf-8") == text
    assert provenance["pairing"]["seed"] == 0 and len(provenance["entries"]) == 15
