"""Reviewed matvec inputs; canonical domain and oracle live in pfc.corpus."""

from pfc.corpus import Case, generate
from pfc.target import Target


def generate_inputs(target: Target, *, seed: int = 0) -> tuple[Case, ...]:
    return generate("matvec", target, seed=seed)
