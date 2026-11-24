# Corpus held-out — conjunto externo selecionado mecanicamente (emenda 34)

Este diretório recebe o conjunto held-out da avaliação de capacidade (desenho §5; ADR-0045, ADR-0054, ADR-0057).
O plano original (kernels escritos pelo orientador a partir de uma lista de 13 nomes) foi substituído em 2026-11-10,
a pedido do usuário, por um conjunto **externo**, selecionado por regra fixa e portado mecanicamente de dois
conjuntos de tarefas do SV-COMP (`sv-benchmarks`, commit `f3feef51a07ffe7c6ef58f3da3b7fe041af4fd25`, Apache-2.0):

- `c/array-tiling` (Chakraborty, Gupta, Unadkat, SAS 2017) — acessos com passo (`a[i*k - j]`), laços aninhados
  decrescentes, cópias reversas, reescritas vizinhas;
- `c/array-multidimensional` (Rajkhowa, Lin, VSTTE 2018) — matrizes 2-D/3-D/4-D achatadas com contrato de capacidade.

## Regra de seleção e port (fixada antes de qualquer execução)

Definida em `pfc_eval/svcomp_arrays.py` (`SELECTION`, `EXCLUDED`, `RULES` R1–R8) e registrada em `PROVENANCE.json`:
toda tarefa `.c` das duas famílias com veredito `unreach-call` verdadeiro, em ordem lexicográfica; exclusão só por
construto fora do subconjunto (`>>`, `sizeof`) ou veredito falso; tipos → `long`; dimensões → parâmetros com
`REQUIRES`; arrays multidimensionais → vetor achatado (`A[i * n + j]`, `m <= A_len / n`); não determinismo →
entradas; a asserção quantificada de conteúdo é descartada (os objetivos de prova são bounds e aritmética de índice);
`for` → `while`; invariante manual B0 = limites mecânicos do contador de cada laço. Uma tarefa cujo B0 o `admit()`
congelado rejeita sai do conjunto **com registro** (`rejected_by_b0`). Perfis de geração e drivers host-C são
derivados pelas mesmas regras. O autor do compilador fez o port: o conjunto é externo, não cego.

```bash
uv run --locked python -m pfc_eval.svcomp_arrays --output heldout --profiles pfc_eval/profiles-heldout \
    --upstream /caminho/para/sv-benchmarks      # uma vez; verifica os digests upstream
uv run --locked python -m pfc_eval heldout seal heldout --untouched --profiles pfc_eval/profiles-heldout \
    --composition reported --prior-check "frontend acceptance" --prior-check "B0 admission by the frozen admit()"
uv run --locked python -m pfc_eval heldout verify heldout
```

A regra de composição B1 é **reportada, não exigida** (`--composition reported`): ajustar uma seleção mecânica à
regra enviesaria o conjunto. As verificações anteriores à selagem (aceitação do frontend e admissão do B0, ambas
determinísticas) ficam nomeadas em `SEALING.json` (`prior_checks`); nenhum proponente, Houdini, oráculo ou
diferencial leu o conjunto antes da selagem.

## Resultado da seleção (2026-11-10, antes da selagem)

49 tarefas nas duas famílias; 3 excluídas por regra (`mlceu`, `skippedu`: veredito falso; `mlceu2`: `>>`/`sizeof`);
46 candidatas portadas; **3 rejeitadas pelo compilador congelado** (`mbpr5`, `pnr4`, `pnr5`: `vc-complexity`, o limite
cumulativo de fórmulas do relatório de VCs, registrado em `PROVENANCE.json`/`rejected`); **43 kernels** no conjunto,
com 90 laços, 209 sítios de guarda executáveis (todos os 43 kernels têm ao menos um), 494 sítios de overflow e 707
obrigações required; o B0 mecânico de todos os 43 foi admitido pelo `admit()` congelado. Composição: 18/43 kernels
(42 %) têm todos os invariantes manuais fora da família B1 (os de passo `k`, cujo limite é `CELLCOUNT / k + 1`, e os
decrescentes); os 25 restantes (matrizes achatadas, `poly`, `rew*`) têm B0 dentro da família. A regra de composição
(≥ 50 %) não é satisfeita e é **reportada**, como decidido antes de a fração ser conhecida (emenda 34).

## Layout

```text
heldout/
    PROVENANCE.json        seleção, regras, exclusões, digests upstream/port/perfil, B0 admitido, sítios de guarda
    LICENSE.Apache-2.0.txt licença upstream
    <nome>/kernel.c        função no subconjunto, com REQUIRES e o B0 mecânico (canário inserido pela selagem)
    <nome>/main.c          driver host-C (runtime)
    pfc_eval/profiles-heldout/<nome>.json   perfil de geração (fora deste diretório; digest em SEALING.json)
    canaries.json          escrito pela selagem
    SEALING.json           escrito pela selagem (aceitação, composição reportada, guardas, perfis, prior_checks)
    HELDOUT.sha256         escrito por último pela selagem
```

## Selagem

A selagem: (1) aceita cada `kernel.c` com o frontend congelado; (2) conta os sítios de guarda pela emissão `checked`
(sem solver) e exige a regra de guardas (≥ metade dos kernels); (3) exige `main.c` e um perfil vinculado para cada
kernel com vetor e registra os digests; (4) avalia a regra de composição (reportada); (5) insere um comentário canário
aleatório por kernel e grava `canaries.json`; (6) grava `SEALING.json`; (7) hasheia todos os arquivos em
`HELDOUT.sha256`. Depois disso nenhuma alteração é permitida: uma tarefa substituída implica novo diretório e nova
selagem, ambos divulgados. Toda campanha que nomeie um caminho sob este diretório verifica o inventário e os canários
antes de qualquer chamada ao solver ou ao serviço (`campaign.run_command` → `heldout.require_intact`).

O endpoint primário é success@5 por condição (L1, L2, B1, B2) e o secundário é a fração de sítios de guarda
removidos por kernel (emenda 31; `pfc_eval/guards.py`). Não há gêmeos ACSL para este conjunto (o oráculo WP fica
restrito aos 14 casos manuscritos de `validation/frama-c/`); o passo `sweep` roda só a ferramenta `pfc`.

## Histórico

`HANDOFF-ORIENTADOR.md` é o pacote do plano anterior (orientador), mantido como registro; está superado pela emenda 34.
