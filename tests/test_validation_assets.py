"""Reproduction assets that the harness pins but never builds: the derived Frama-C image with Z3 (plan phase 6)."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / "validation" / "frama-c" / "manifest.json").read_text(encoding="utf-8"))["image"]


def test_frama_c_z3_dockerfile_pins_the_base_digest_and_sets_a_global_why3_config():
    dockerfile = (ROOT / "validation" / "frama-c-z3" / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.splitlines()[0].strip() == f"FROM {BASE}"
    assert "z3" in dockerfile and "why3 config detect" in dockerfile and "WHY3CONFIG=/etc/why3.conf" in dockerfile
    assert re.search(r"^USER \S+", dockerfile, re.M)  # the runner passes --user; a global config is required
    readme = (ROOT / "validation" / "frama-c-z3" / "README.md").read_text(encoding="utf-8")
    assert "docker build" in readme and "image inspect" in readme and "--prover z3" in readme
    manifest = json.loads((ROOT / "validation" / "frama-c-z3" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["base_image"] == BASE and manifest["schema_version"] == 1
    assert set(manifest) >= {"base_image", "image_id", "dockerfile_sha256", "z3_version", "why3_provers", "built"}
