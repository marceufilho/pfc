UV ?= uv
export PFC_CLANG ?= clang-19
KERNELS ?= kernels
BUILD_OUTPUT ?= build/local
BUILD_MODE ?= verified
BUILD_FACTS ?= delete,restrict,assume
BUILD_ARGS ?=
VERIFY_ARGS ?=
TEST_ARGS ?=
SLICE_OUTPUT ?= build/g0
SLICE_ARGS ?=
EVAL_ARGS ?= --help

.PHONY: help sync check verify build test slice eval freeze-check

help:
	@echo "sync          create the locked Python environment"
	@echo "check         tests, lint, format check and types"
	@echo "verify        prove the obligations of the programs in KERNELS (default: kernels)"
	@echo "build         emit and compile them: BUILD_MODE=plain|checked|verified, BUILD_OUTPUT=NEW_DIR"
	@echo "test          1000-input differential test of the three builds of each kernel"
	@echo "slice         the sum experiment: a proof removes a check; without the precondition it returns"
	@echo "eval          the evaluation harness: make eval EVAL_ARGS='differential kernels --output NEW --seed 0'"
	@echo "freeze-check  compare the compiler files with the recorded SHA-256 inventory"

sync:
	$(UV) sync --locked --dev

check:
	$(UV) run --locked pytest
	$(UV) run --locked ruff check .
	$(UV) run --locked black --check --line-length 120 .
	$(UV) run --locked mypy pfc
	$(UV) run --locked mypy --strict pfc_eval

verify:
	$(UV) run --locked pfc verify $(KERNELS) $(VERIFY_ARGS)

build:
	$(UV) run --locked pfc build $(KERNELS) --mode $(BUILD_MODE) --facts "$(BUILD_FACTS)" --output "$(BUILD_OUTPUT)" $(BUILD_ARGS)

test:
	$(UV) run --locked pfc test $(KERNELS) $(TEST_ARGS)

slice:
	$(UV) run --locked pfc slice kernels/sum/kernel.c --output "$(SLICE_OUTPUT)" $(SLICE_ARGS)

eval:
	$(UV) run --locked python -m pfc_eval $(EVAL_ARGS)

freeze-check:
	sha256sum --check --quiet validation/freeze/protected.sha256
