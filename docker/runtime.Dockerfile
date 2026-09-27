FROM ghcr.io/openhands/agent-canvas:1.24.0@sha256:ad0829a7082a71ddfd2d16c1fae5a2172e4ca5a34eba7f03b69bd5b9b1bc54d7
USER root
# OCR delegation performs no model calls. Pin its executable and matching skill.
ADD --checksum=sha256:4d2c4f39a98d3e26ac0b76d5f0af304c661f5dad12937c39b2cba4e8e92adeaf --chmod=755 https://github.com/alibaba/open-code-review/releases/download/v1.12.4/opencodereview-linux-amd64 /usr/local/bin/ocr
ADD --checksum=sha256:2046da3cf30a4b672236c66f707d02383de5792498a8e6d7b9fece6be2c212b9 https://codeload.github.com/alibaba/open-code-review/tar.gz/refs/tags/v1.12.4 /tmp/alibaba-review.tar.gz
ADD --checksum=sha256:53e708ebf770dfe35b57df0586a5058707081a870cd149ce30a2d57c51ed29b6 https://codeload.github.com/cloudflare/security-audit-skill/tar.gz/c1c8a8c1471069fb0e188eeaff69b8e8db6564a8 /tmp/cloudflare-audit.tar.gz
RUN mkdir -p /opt/factory/reviewers/alibaba /opt/factory/reviewers/cloudflare && \
    tar -xzf /tmp/alibaba-review.tar.gz -C /opt/factory/reviewers/alibaba --strip-components=1 --wildcards '*/skills/open-code-review-delegate/*' '*/LICENSE' && \
    tar -xzf /tmp/cloudflare-audit.tar.gz -C /opt/factory/reviewers/cloudflare --strip-components=1 && \
    rm /tmp/alibaba-review.tar.gz /tmp/cloudflare-audit.tar.gz && \
    ocr version && git --version
COPY runtime/install_reviewers.py /opt/factory/install_reviewers.py
COPY runtime/ocr-rule.json /opt/factory/reviewers/rule.json
RUN python /opt/factory/install_reviewers.py && chmod -R a+rX /opt/factory/reviewers
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
COPY runtime/patch_acp_usage.py /opt/factory/patch_acp_usage.py
COPY runtime/factory-usage.mjs /opt/factory/factory-usage.mjs
RUN python /opt/factory/patch_acp_usage.py
COPY runtime/patch_workspace_runtime.py /opt/factory/patch_workspace_runtime.py
RUN python /opt/factory/patch_workspace_runtime.py
COPY runtime/patch_run_outcomes.py /opt/factory/patch_run_outcomes.py
RUN python /opt/factory/patch_run_outcomes.py
COPY runtime/patch_download_filename.py /opt/factory/patch_download_filename.py
RUN python /opt/factory/patch_download_filename.py
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
