# Derived image for the second WP adjudication (E26, AMENDMENTS 39): the sealed oracle image plus the Z3 release
# that pfc itself uses (z3-solver 5.3.3.0 in uv.lock), so that the adjudication separates "version of Z3" from
# "encoding of WP". The binary comes from the same PyPI wheel as the locked environment (manylinux, self-contained).
FROM python:3.12-slim AS z3
RUN pip install --no-cache-dir z3-solver==5.3.3.0 && z3 --version

FROM framac/frama-c@sha256:4f0b7a2bd600c4daabfdcdfa393a3c18301e0db18f3b2739f1d9650193462d36
USER root
ENV PATH="/home/opam/.opam/4.14/bin:${PATH}"
COPY --from=z3 /usr/local/bin/z3 /usr/local/bin/z3
RUN z3 --version
# The runner passes --user uid:gid of the host, so a per-home ~/.why3.conf is never found: write a global one.
RUN why3 config detect -C /etc/why3.conf && why3 config list-provers -C /etc/why3.conf
ENV WHY3CONFIG=/etc/why3.conf
USER opam
