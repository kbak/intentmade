ARG FACTORY_IMAGE=intentmade:dev
FROM ${FACTORY_IMAGE}
USER root
# Docker's native v2 Kit launch contract for the existing OpenHands server.
RUN useradd --create-home --uid 1000 --shell /bin/bash agent && \
    install -d -o agent -g agent /home/agent/.config && \
    usermod -aG docker agent && \
    printf '%s\n' 'agent ALL=(ALL) NOPASSWD:ALL' \
      'Defaults env_keep += "HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy"' \
      > /etc/sudoers.d/sandbox-agent && chmod 0440 /etc/sudoers.d/sandbox-agent
USER agent
WORKDIR /home/agent
ENTRYPOINT []
CMD ["/bin/bash"]
