ARG FACTORY_IMAGE=intentmade:dev
FROM ${FACTORY_IMAGE}
USER root
ARG CONTROLLER_UID
ARG CONTROLLER_GID
# Native sbx credentials and its socket belong to the host operator. Keep the
# upstream OpenHands user/home while matching that operator's filesystem identity.
RUN test "${CONTROLLER_UID}" -gt 0 && test "${CONTROLLER_GID}" -gt 0 && \
    (getent group "${CONTROLLER_GID}" || groupadd -g "${CONTROLLER_GID}" sandbox-operator) && \
    usermod -u "${CONTROLLER_UID}" -g "${CONTROLLER_GID}" openhands && \
    mkdir -p /home/openhands/.config && \
    chown -R "${CONTROLLER_UID}:${CONTROLLER_GID}" /home/openhands /projects
USER openhands
