# Optional pilot runtime. Build the ordinary factory image from this checkout first.
ARG BASE_IMAGE=openhands-factory:dev
FROM ${BASE_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-21-jre-headless && rm -rf /var/lib/apt/lists/*
# An explicit build context supplies versioned wheels and the checksum-pinned JAR.
# It may come from local builds or a release artifact; no sibling source is imported.
COPY --from=traceability_wheels / /opt/factory/traceability-tools/
RUN python -m pip install --no-index --find-links=/opt/factory/traceability-tools \
      versioned-traceability==0.4.1 openhands-traceability==0.2.2 && \
    python -c "from pathlib import Path; from versioned_traceability.oft import validate_jar; validate_jar(Path('/opt/factory/traceability-tools/openfasttrace-4.9.0.jar'))"
ENV VT_OFT_JAR=/opt/factory/traceability-tools/openfasttrace-4.9.0.jar
USER openhands
