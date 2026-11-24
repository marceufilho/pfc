# Imagem derivada: Frama-C selado + Z3 5.3.3 (E26, emenda 39)

Mesma base da imagem do oráculo (`validation/frama-c`), com o binário Z3 da versão que o `pfc` usa (`z3-solver 5.3.3.0`).
Serve só à segunda adjudicação da célula `matvec`: separa "versão do Z3" de "codificação do WP".

```bash
docker build -t pfc-frama-c-z3-533 validation/frama-c-z3-5.3.3
uv run --locked python -m pfc_eval sweep kernels bench/external/acsl --tools wp --prover z3 \
  --image sha256:<id> --budgets 10,30,60 --output out/wp-z3
```

O Why3 da imagem não reconhece a versão 5.3.3 e a registra com o driver de Z3 mais recente que conhece; isso é
declarado em `manifest.json`.
