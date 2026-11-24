# Casos negativos do subconjunto

`cases.json` fixa regra, linha e coluna esperadas para cada um dos 24 fontes pequenos. São expectativas
escritas independentemente do diagnóstico, não geradas a partir do resultado do compilador. Cada caso
passa pelo frontend e pela resolução tipada; a primeira regra aplicável determina o diagnóstico.

Executar: `uv run --locked pytest tests/test_subset.py -k negative_fixture -q`.
Esperado: 24 testes passando por observar exatamente a rejeição e a posição esperadas.

Exemplo CLI existente: `uv run --locked pfc ir tests/negative/pointer-arith.c --json`.
Esperado: código 1, stdout vazio, regra subset-pointer-arith em stderr, linha 2/coluna 12.
Pycparser/cpp produzem coordenadas lógicas; não se promete posição byte a byte de expansões arbitrárias.

Regras sintáticas vivem em pfc/subset.py; nomes/tipos/const/comprimentos reutilizam pfc/lower.py. O teste
unitário acrescenta recursão indireta, visibilidade, assinaturas e posições de chamadas.
