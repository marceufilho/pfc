# Examples

Six small programs in the pfc subset. `kernel.c` is the compiler's input; `main.c` is an ordinary C driver.

| Example | Shows | Guards checked → verified | Driver prints |
| --- | --- | ---: | --- |
| `01_first` | array read under a precondition | 1 → 0 | `7` |
| `02_clamp` | `if`, returns and `ENSURES` | 0 → 0 | `0 42 100` |
| `03_divide` | defined division | 1 → 0 | `7` |
| `04_count` | `while`, induction, postcondition | 0 → 0 | `5` |
| `05_zero` | `for` and array writes | 1 → 0 | `3: 0 0 0 4` |
| `06_calls` | contracts of non-recursive calls | 0 → 0 | `5` |

```bash
uv run --locked pfc check examples/01_first/kernel.c
uv run --locked pfc emit examples/01_first/kernel.c --mode verified
uv run --locked pfc build examples --mode verified --output build/examples   # the output directory must be new
./build/examples/01_first/program
```
