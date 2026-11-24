# Held-out generation profiles

One `pfc-eval-inputs-v1` profile per held-out array kernel, written by the kernel author with the kernel (never by
the compiler/harness author), bound to the kernel text by `program_sha256` and recorded by digest in
`heldout/SEALING.json`. `python -m pfc_eval heldout skeleton heldout --profiles pfc_eval/profiles-heldout` writes a valid starting point per
array kernel (never overwriting); edit the ranges and add boundary cases, then seal. `python -m pfc_eval differential heldout --profile pfc_eval/profiles-heldout` reads them.
