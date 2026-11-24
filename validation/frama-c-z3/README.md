# Derived Frama-C image with Z3 (WP adjudication of the matvec cell)

The original oracle run used Frama-C 32.1 WP with Alt-Ergo 2.5.4 only; the pinned image
carries no other prover, and the `matvec` bound `0 <= i*cols + j < matrix_len` (nonlinear) timed out at every budget
while pfc/Z3 proves it. To adjudicate whether the cause is the prover or the encoding, this directory derives an image
from the **same pinned digest** and adds Z3 as a Why3 prover. The harness never builds or pulls images: the user runs
the commands below and records the result here; `pfc_eval sweep --prover z3 --image sha256:<id>` then uses it.

## Build and record (user)

```bash
docker build -t pfc-frama-c-z3 validation/frama-c-z3
docker image inspect --format '{{.Id}}' pfc-frama-c-z3            # -> sha256:<64 hex>, the pinned identity
docker run --rm --user 12345:12345 pfc-frama-c-z3 why3 config list-provers -C /etc/why3.conf   # must list Z3
docker run --rm pfc-frama-c-z3 z3 --version
sha256sum validation/frama-c-z3/Dockerfile
```

Fill `manifest.json` with `image_id`, `z3_version`, the `why3_provers` output and the build date (`built`), then run:

```bash
uv run --locked python -m pfc_eval sweep kernels bench/external/acsl --tools wp --prover z3 \
  --image sha256:<id> --budgets 10,30,60 --output out/wp-z3
```

The sealed oracle directory is never touched; the adjudication is a separate row in the oracle table. If Z3 under
WP proves `typed_matvec_assert_bounds_matrix`, the cause was prover automation; if it does not, the cause remains
unestablished and the prover is not the sole explanation.
