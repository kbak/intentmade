# Optional proof runtime; BASE_IMAGE must be the matching factory/traceability build.
ARG BASE_IMAGE=intentmade:traceability-test
FROM ${BASE_IMAGE}
USER root
ADD --checksum=sha256:05410730774ba7957678184bf2c2a7bc7ce7238e1a873277371e4d586f7cd301 https://codeload.github.com/math-inc/OpenGauss/tar.gz/f87633900ae185b8037bf451a914fe7eeae1eb08 /tmp/opengauss.tar.gz
ADD --checksum=sha256:c6c039ccace95b971f6c7522bd5ccc2bbf4a82a8df2a98728891dda54b9991f0 https://codeload.github.com/cameronfreer/lean4-skills/tar.gz/b6243b85b9b0a0ddff5bb6773889044daf687f8e /tmp/lean4-skills.tar.gz
RUN mkdir -p /opt/factory/opengauss/source /opt/factory/opengauss/lean4-skills && \
    tar -xzf /tmp/opengauss.tar.gz -C /opt/factory/opengauss/source --strip-components=1 && \
    tar -xzf /tmp/lean4-skills.tar.gz -C /opt/factory/opengauss/lean4-skills --strip-components=1 && \
    rm /tmp/opengauss.tar.gz /tmp/lean4-skills.tar.gz
COPY runtime/opengauss-requirements.txt /opt/factory/opengauss/requirements.txt
RUN python -m venv /opt/factory/opengauss/venv && \
    /opt/factory/opengauss/venv/bin/python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
      -r /opt/factory/opengauss/requirements.txt && \
    /opt/factory/opengauss/venv/bin/python -m pip check
COPY runtime/opengauss-pins.json /opt/factory/opengauss/pins.json
COPY runtime/opengauss_runtime.py /opt/factory/opengauss_runtime.py
COPY --chmod=755 runtime/opengauss-codex-acp /opt/factory/opengauss-codex-acp
RUN python -c 'import sys; sys.path.insert(0,"/opt/factory/opengauss/source"); import gauss_cli.autoformalize'
USER openhands
