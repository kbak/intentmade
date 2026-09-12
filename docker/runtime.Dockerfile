FROM ghcr.io/openhands/agent-canvas:1.18.0@sha256:64d73ec6c066b425e872e1a58d52d8b5d901b617a8e945b1430415c5fd4ea4d1
USER root
# Pin the Codex/ACP versions used by the factory integrations.
RUN PATH="/acp-node/bin:$PATH" /acp-node/bin/npm install --global \
    @agentclientprotocol/codex-acp@1.10.0 @openai/codex@0.153.4
# Browser tools are forwarded through native ACP MCP configuration for QA workers.
RUN PATH="/acp-node/bin:$PATH" /acp-node/bin/npm install --global @playwright/mcp@0.0.80
COPY runtime/patch_review_policy.py /opt/factory/patch_review_policy.py
RUN python /opt/factory/patch_review_policy.py
COPY runtime/patch_agency_agents.py /opt/factory/patch_agency_agents.py
RUN python /opt/factory/patch_agency_agents.py
COPY runtime/patch_specialist_review.py /opt/factory/patch_specialist_review.py
RUN python /opt/factory/patch_specialist_review.py
COPY runtime/patch_workspace_runtime.py /opt/factory/patch_workspace_runtime.py
RUN python /opt/factory/patch_workspace_runtime.py
COPY runtime/patch_run_outcomes.py /opt/factory/patch_run_outcomes.py
RUN python /opt/factory/patch_run_outcomes.py
COPY runtime/reply_hook.py /opt/agent-canvas/tools/factory_reply_hook.py
RUN python - <<'PY'
from pathlib import Path
path = Path('/opt/agent-canvas/entrypoint.sh')
source = path.read_text()
old = 'AGENT_SERVER_IMPORT_MODULES="canvas_ui_tool"'
if source.count(old) != 1:
    raise RuntimeError('Pinned Canvas extension loader changed; review reply integration')
path.write_text(source.replace(old, 'AGENT_SERVER_IMPORT_MODULES="canvas_ui_tool,factory_reply_hook"'))
PY
ADD --checksum=sha256:75d15075b678c87f48d42efb78fd9e6705e0d557fdf6d689a2a6175ab89f1ce3 https://raw.githubusercontent.com/OpenHands/extensions/39fc25a91749fe248db391315c2c4eb2c74655a6/skills/github-issue-to-pr/scripts/main.py /opt/factory/upstream/issues.py
ADD --checksum=sha256:d3c38b6f79bb024774c09e1f0600f531a8ca658ba6798c9ba1269d0bca4dcef5 https://raw.githubusercontent.com/OpenHands/extensions/39fc25a91749fe248db391315c2c4eb2c74655a6/skills/github-pr-reviewer/scripts/main.py /opt/factory/upstream/reviews.py
ADD --checksum=sha256:fe425248fc51d1d1805ab1442ba8add98d770ad8a132051eaac8c93d7eee3ebe https://raw.githubusercontent.com/OpenHands/extensions/39fc25a91749fe248db391315c2c4eb2c74655a6/LICENSE /opt/factory/upstream/LICENSE
COPY runtime/entrypoint /opt/factory/entrypoint
COPY workflows/ /opt/factory/workflows/
COPY runtime/factory_context.py /opt/factory/workflows/factory_context.py
COPY runtime/factory-context.mjs /opt/factory/factory-context.mjs
COPY runtime/factory-agents.mjs /opt/factory/factory-agents.mjs
COPY runtime/factory-review.mjs /opt/factory/factory-review.mjs
COPY runtime/codex-seccomp.json /opt/factory/codex-seccomp.json
COPY runtime/LICENSE.moby-profiles /opt/factory/LICENSE.moby-profiles
COPY coordinator/AGENTS.md /opt/factory/coordinator.md
COPY scripts/configure.py /opt/factory/configure.py
COPY upstream.lock.json /opt/factory/upstream.lock.json
RUN chmod -R a+rX /opt/factory/upstream && chmod +x /opt/factory/entrypoint
# Pin agency-agents and use its native Codex converter. Only the generated
# instructions and license remain in the runtime; no host Codex home is mounted.
ADD --checksum=sha256:bac8380e180c047dd21bb90653da6404559108f88d4c93bd36d056bd522d74dc https://codeload.github.com/msitarzewski/agency-agents/tar.gz/647c8baa42b6842afb4a97bf2c0950d45ba88e8b /tmp/agency-agents.tar.gz
RUN mkdir /tmp/agency-agents && \
    tar -xzf /tmp/agency-agents.tar.gz -C /tmp/agency-agents --strip-components=1 && \
    /tmp/agency-agents/scripts/convert.sh --tool codex > /tmp/agency-convert.log && \
    CODEX_AGENTS_DIR=/opt/factory/agency-agents/agents \
      /tmp/agency-agents/scripts/install.sh --tool codex --no-interactive --no-convert && \
    cp /tmp/agency-agents/LICENSE /opt/factory/agency-agents/LICENSE && \
    chmod -R a+rX /opt/factory/agency-agents && \
    rm -rf /tmp/agency-agents /tmp/agency-agents.tar.gz /tmp/agency-convert.log
USER openhands
ENV PYTHONPATH=/opt/factory/workflows
ENV OH_CONVERSATIONS_PATH=/home/openhands/.openhands/conversations
ENV OH_PERSISTENCE_DIR=/home/openhands/.openhands
ENV OH_BASH_EVENTS_DIR=/home/openhands/.openhands/bash_events
# Workspace permission profiles require the modern namespace sandbox. Both
# Canvas and native DockerWorkspace workers use the matching seccomp profile.
ENV CODEX_CONFIG='{"features.use_legacy_landlock":false}'
ENV FACTORY_SECCOMP_PROFILE=/opt/factory/codex-seccomp.json
WORKDIR /projects
ENTRYPOINT ["tini", "--", "/opt/factory/entrypoint"]
