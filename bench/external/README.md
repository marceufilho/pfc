# External programs

Third-party programs ported to the pfc subset. Each directory keeps its own licence and a `PROVENANCE.json`
with the upstream revision, the selection rule and every exclusion.

| Directory | Origin | Licence |
| --- | --- | --- |
| `acsl/` | eight functions of *ACSL by Example* (Fraunhofer FOKUS), originals in `acsl/upstream/` | MIT |
| `zilu/` | 30 loops of the Zilu benchmarks (`c/loop-zilu` of SV-COMP sv-benchmarks) | GPL-2.0 |
| `zilu-obfuscated/` | the same 30 loops with identifiers renamed (record in `zilu/PROVENANCE.json`) | GPL-2.0 |
| `zilu-composed/` | 15 two-loop programs composed from the Zilu loops | GPL-2.0 |

```bash
uv run --locked pfc verify bench/external/acsl
uv run --locked pytest -q tests/test_external_slice.py
uv run --locked python -m pfc_eval.ports --output NEW_DIR --seed 0      # regenerate the Zilu ports
```
