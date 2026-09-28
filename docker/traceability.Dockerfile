# Optional traceability runtime. Build the ordinary factory image first.
ARG BASE_IMAGE=intentmade:dev
FROM ${BASE_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-21-jre-headless && rm -rf /var/lib/apt/lists/*
# An explicit build context supplies versioned wheels and the checksum-pinned JAR.
# It may come from local builds or a release artifact; no sibling source is imported.
COPY --from=traceability_wheels / /opt/factory/traceability-tools/
COPY docker/traceability-requirements.txt /opt/factory/traceability-requirements.txt
RUN python -m pip install --no-index --find-links=/opt/factory/traceability-tools \
      --require-hashes --only-binary=:all: -r /opt/factory/traceability-requirements.txt && \
    python -m pip install --no-index --no-deps --find-links=/opt/factory/traceability-tools \
      intentbond==0.4.6 && \
    python -c "from pathlib import Path; from intentbond.oft import validate_jar; validate_jar(Path('/opt/factory/traceability-tools/openfasttrace-4.9.0.jar'))"
ENV INTENTBOND_OFT_JAR=/opt/factory/traceability-tools/openfasttrace-4.9.0.jar
USER openhands
