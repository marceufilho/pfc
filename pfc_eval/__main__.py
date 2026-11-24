"""Standalone entry point: `uv run --locked python -m pfc_eval` from the repository root."""

import sys

from pfc_eval.cli import main

sys.exit(main())
