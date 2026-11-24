FROM framac/frama-c@sha256:4f0b7a2bd600c4daabfdcdfa393a3c18301e0db18f3b2739f1d9650193462d36
# Derived image for the WP adjudication run (ADR on the matvec cell): the sealed oracle image plus Z3 as a Why3
# prover. Built and pinned by the user; the harness only inspects the recorded image id and never builds or pulls.
USER root
# why3 and alt-ergo live in the opam switch of the base image; make them visible to root and to any --user.
ENV PATH="/home/opam/.opam/4.14/bin:${PATH}"
RUN apt-get update && apt-get install -y --no-install-recommends z3 && rm -rf /var/lib/apt/lists/* && z3 --version
# The runner passes --user uid:gid of the host, so a per-home ~/.why3.conf is never found: write a global one.
RUN why3 config detect -C /etc/why3.conf && why3 config list-provers -C /etc/why3.conf
ENV WHY3CONFIG=/etc/why3.conf
USER opam
