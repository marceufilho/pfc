> **Superado em 2026-11-10 (emenda 34).** O usuário optou por um conjunto held-out externo, selecionado
> mecanicamente do SV-COMP e portado pelas regras de `pfc_eval/svcomp_arrays.py`, sem orientador. Este pacote fica
> como registro do plano anterior; leia `heldout/README.md`.

# Entrega ao orientador — corpus held-out da avaliação pré-registrada

Data da entrega: 2026-11-09. Este documento é tudo o que o autor dos kernels precisa receber; **nenhum conteúdo dos
kernels deve voltar ao autor do compilador antes da selagem**. O autor do compilador/harness não escreve, não lê e não
sugere kernels: a validade do estudo depende disso (ADR-0045, ADR-0054; emendas 14, 30 e 31).

## O que pedimos

Treze funções em C no subconjunto do `pfc`, uma por diretório `heldout/<nome>/`, com contratos e invariante manual, mais
um driver host-C por kernel com vetor e um perfil de geração por kernel com vetor. Nomes fixos (substituições são
registradas na selagem): `strided_sum` (passo ≥ 2), `compact` (ou `copy_if`; índice de escrita ≤ índice de leitura),
`partition`, `argmax`, `two_sum_sorted` (dois ponteiros), `running_max`, `transpose`, `insertion_sort` (laço interno),
`reverse`, `count_if`, `max_element`, `pairwise_diff` (índices i e i+1), `window_sum` (janela fixa ≥ 2).

Regras de composição que a selagem verifica mecanicamente:

1. **Guardas executáveis**: ao menos metade dos kernels tem um acesso indexado `a[i]` ou uma divisão executável.
2. **Fora da família de templates**: ao menos metade dos laços tem invariante manual que **não** é só da forma
   `c <= v` / `v <= w + c` com `c ∈ {−1, 0, 1}` (passo não unitário, constante além de ±1, relação entre três
   variáveis, igualdade como `s == i * k`, disjunção).
3. Todo kernel com vetor tem `main.c` e um perfil em `pfc_eval/profiles-heldout/<nome>.json`.

## O subconjunto aceito (resumo; manual completo em `docs/manual/README.md` e `docs/kernel-contracts.md`)

`long`, `bool`, vetores com comprimento explícito como par de parâmetros `const long a[], long a_len` (ou mutável sem
`const`), `while`/`for` estruturados, `if`/`else`, atribuições, retorno, chamadas não recursivas a funções do mesmo
arquivo. Contratos: `REQUIRES(...)` no início da função, `INVARIANT(...)` como primeira instrução do corpo do laço,
`ENSURES(...)`, `ASSERT(...)`, `SEPARATED(a, a_len, b, b_len)` dentro de `REQUIRES`. Sem ponteiros aritméticos,
`i++`, `+=`, structs, floats, recursão ou alocação.

Exemplo mínimo com guarda e invariante fora da família (não use este exatamente; é só a forma):

```c
#include "spec.h"

long strided_sum(const long a[], long a_len, long n, long stride)
{
    REQUIRES(0 <= n && n <= a_len && 2 <= stride && stride <= 4);
    long s = 0;
    long i = 0;
    while (i < n) {
        INVARIANT(0 <= i && i <= n + stride - 1);
        s = s + a[i];
        i = i + stride;
    }
    return s;
}
```

`main.c` é um driver host-C comum (fora do subconjunto) que chama a função; `kernels/sum/main.c` é um modelo.

## Comandos (no clone do repositório, com `uv sync --locked --dev` feito)

```bash
# 1. aceitação de cada kernel pelo frontend congelado (sem solver, sem prova)
uv run --locked pfc ir heldout/<nome>/kernel.c
# 2. perfis iniciais para os kernels com vetor (edite intervalos e acrescente casos de fronteira; nunca sobrescreve)
uv run --locked python -m pfc_eval heldout skeleton heldout --profiles pfc_eval/profiles-heldout
# 3. selagem única (declaração explícita de que nenhum experimento leu o diretório) e verificação
uv run --locked python -m pfc_eval heldout seal heldout --untouched --profiles pfc_eval/profiles-heldout
uv run --locked python -m pfc_eval heldout verify heldout
make doctor DOCTOR_ARGS='--heldout heldout'
```

Edite o `kernel.c` **antes** de gerar o perfil: o perfil fica vinculado ao texto do kernel por `program_sha256`.
Depois da selagem nada muda; um kernel substituído implica um novo diretório e uma nova selagem, ambas divulgadas.

## O que não fazer

- Não rodar `proposer`, `houdini`, `differential` nem o oráculo WP sobre `heldout/` antes da selagem.
- Não mostrar os kernels, os perfis ou os canários ao autor do compilador até a campanha (`eval-v1`) terminar.
- Não copiar laços dos estratos públicos (`kernels/`, `bench/external/`): os canários não detectam isso; a composição
  é responsabilidade de quem escreve.

## Depois da entrega

A campanha roda uma vez, em ordem, na máquina designada (`pfc_eval/campaign_plan.py`), com os resultados entrando em
`paper/evidence.json` sem alteração dos kernels. O desfecho primário é success@5 e o secundário é a fração de sítios
de guarda removidos por kernel (emenda 31), com os contrastes pré-registrados nas emendas 26, 31 e 32.
