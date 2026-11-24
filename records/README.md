# Records

Small records of the runs behind the reported results. The raw runs (observations, logs, emitted C, proposer
transcripts) are not published; what is here lets a reader check a reported number against the summary that
produced it, and see what was fixed in advance.

- `design/experimental-design.md`: hypotheses, thresholds, baselines and statistics, as registered.
- `design/AMENDMENTS.md`: every later change, dated, including the post hoc analyses (Portuguese).
- `design/preregistration-20261109/`: the bundle of 2026-11-09 with its checksums. Dates are local: no external
  timestamp exists.
- `design/evidence.json`, `design/SOURCES.json`: which run feeds which table, with the manifest digest of each run.
- `runs/<name>/`: for each sealed run, `configuration.json` (settings, seed, compiler identity),
  `environment.json`, `summary.json`, `tables.md`, `schema.json` and `manifest.json` (SHA-256 of every file of the
  run, including the unpublished ones).

Some configuration files contain paths of the machine the runs were made on. Rerunning an experiment (see the
top-level README) writes a directory with the same layout; rows that depend on a language model will not repeat
exactly.
