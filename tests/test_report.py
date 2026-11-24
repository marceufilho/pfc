import json
from dataclasses import replace

import pytest

from pfc import report
from pfc.diagnostics import FrontendError


def summary():
    entry = report.Row("kernel", 4, 3, 1, 0, 0, 0, True, 1, 1, 1000, 1000, True, True)
    return report.Summary("test", (entry,), True)


def test_round_trip_and_deterministic_tables(tmp_path):
    report.seal(tmp_path, summary())
    loaded = report.load(tmp_path)
    assert loaded == summary()
    assert report.render(loaded) == report.render(summary())
    assert "1000/1000" in report.render(loaded)


@pytest.mark.parametrize("change", ["negative", "nan", "bool", "counts", "success", "empty"])
def test_malformed_summary_cannot_be_reported_as_success(tmp_path, change):
    from pfc.ir import to_data

    data = to_data(summary())
    if change == "negative":
        data["rows"][0]["total"] = -1
    if change == "nan":
        data["rows"][0]["total"] = float("nan")
    if change == "bool":
        data["rows"][0]["total"] = True
    if change == "counts":
        data["rows"][0]["proved"] = 99
    if change == "success":
        data["rows"][0]["breakit_passed"] = False
    if change == "empty":
        data["rows"] = []
    raw = json.dumps(data).encode()
    (tmp_path / "report.json").write_bytes(raw)
    import hashlib

    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "files": {"report.json": hashlib.sha256(raw).hexdigest()}})
    )
    with pytest.raises(FrontendError, match="report-input"):
        report.load(tmp_path)


def test_manifest_cannot_reference_outside_artifact_directory(tmp_path):
    directory = tmp_path / "out"
    directory.mkdir()
    (tmp_path / "secret").write_text("outside")
    (directory / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "files": {"report.json": "bad", "../secret": "bad"}})
    )
    with pytest.raises(FrontendError, match="report-input"):
        report.load(directory)


def test_reporting_a_failed_experiment_does_not_change_its_status(tmp_path):
    good = summary()
    failed = replace(good, passed=False, rows=(replace(good.rows[0], passed=False, breakit_passed=False),))
    report.seal(tmp_path, failed)
    assert not report.load(tmp_path).passed
    assert "failed" in report.render(report.load(tmp_path))


def test_schema_3_round_trip_records_emitter_and_overflow_counts(tmp_path):
    from pfc.emit import EMITTER

    entry = report.Row("kernel", 4, 3, 1, 0, 0, 0, True, 1, 1, 1000, 1000, True, True, None, 3, 2)
    current = report.Summary("test", (entry,), True)
    assert current.schema_version == 3 and current.emitter == EMITTER
    report.seal(tmp_path, current)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["schema_version"] == 3 and manifest["emitter"] == EMITTER
    loaded = report.load(tmp_path)
    assert loaded == current and loaded.rows[0].overflow_checks == 3 and loaded.rows[0].overflow_removed == 2
    assert "overflow" in report.render(loaded)


def test_manifest_emitter_mismatch_is_rejected(tmp_path):
    entry = report.Row("kernel", 4, 3, 1, 0, 0, 0, True, 1, 1, 1000, 1000, True, True, None, 3, 2)
    report.seal(tmp_path, report.Summary("test", (entry,), True))
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["emitter"] = "emitter-v4"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(FrontendError, match="report-input"):
        report.load(tmp_path)
    bad = report.Row("kernel", 4, 3, 1, 0, 0, 0, True, 1, 1, 1000, 1000, True, True, None, 1, 2)  # removed > checks
    other = tmp_path / "other"
    other.mkdir()
    report.seal(other, report.Summary("test", (bad,), True))
    with pytest.raises(FrontendError, match="report-input"):
        report.load(other)
