# Headers somente para parsing

`stdbool.h` fornece as quatro macros C11 necessárias para bool/true/false, sem copiar a libc do sistema.
O frontend usa `-nostdinc`; headers ausentes, como stdio.h, são rejeitados no pré-processamento.
Não usar estes headers para compilar os drivers ou alegar um modelo de ABI/memória.

Fonte canônica para o wheel: este diretório, mapeado por Hatch para `pfc/_resources/fake_libc`.
O header foi escrito para este projeto; não é uma cópia do conjunto de fake headers de pycparser.
Ver `docs/decisions/0005-syntax-frontend.md` e a referência C11 N1570, §7.18.
