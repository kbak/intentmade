# Runtime patch review

The runtime pins Canvas 1.26.0 by image digest, with OpenHands SDK/Workspace/Agent
Server 1.53.0 and Automation 1.19.0. Review patches against the installed source
when updating that pin; version guards and exact source matches deliberately
stop the build on drift.

| Integration | Decision for Canvas 1.26.0 | Reason |
| --- | --- | --- |
| Unicode automation downloads | Removed | Automation now sanitizes names, supplies an ASCII fallback and encodes Unicode with `filename*`. The regression exercises the installed upstream route, including compatibility characters. |
| Review permissions and project trust | Reduced | ACP 2.2.2 supplies the native read-only sandbox; the patch only disables escalation and automatic project trust. SDK's permission bridge still needs its read-only denial. The launcher uses ACP's `DISABLE_MCP_CONFIG_FILTERING=true` option so ignored repository configuration cannot suppress explicit factory MCP servers. |
| Startup failures | Retained | SDK still classifies a ChatGPT authentication timeout as rejected credentials and sets terminal error state before emitting its reason. ACP still starts interactive login unless the factory's headless check rejects it. |
| Native agent plugin isolation | Retained | SDK still loads user and project plugins for local conversations. Factory workers and report readers require only explicitly selected tools. |
| Workspace runtime | Retained | DockerWorkspace still needs the factory seccomp profile; ACP session entry points still need factory path mapping and explicit session configuration. |
| Specialist review evidence | Retained | ACP's native history reader supplies legacy or paginated thread history. The factory still needs its role-bound final review receipt. |
| ACP usage accounting | Reduced | ACP 2.2.2 supplies whole-prompt accumulation, cache accounting and response conversion. The factory retains raw provider evidence and an unknown-value guard for missing baselines, resets and invalid counters; native fallback counts alone do not establish a measured delta. |
| Automation outcomes | Retained | Callback schema still accepts only COMPLETED/FAILED, drops task outcome metadata and cannot associate a conversation with phase updates. Native SKIPPED rendering alone does not supply that callback contract. |
| PR archive bounds | Retained | This patches the separately pinned Extensions downloader, which still reads the response and archive member list without the factory's resource bounds. The Canvas update does not change its pinned source. |
| Python Agent Server launcher | Retained | Canvas still invokes `openhands-agent-server`; the factory wrapper ensures both controller and workers execute the patched Python packages and register the read-only report tool. |
| Canvas reply hook | Retained | The upstream entrypoint imports `canvas_ui_tool`; the factory additionally needs its existing reply dispatcher. |

The factory's npm lock selects Codex ACP 2.2.2 and its compatible Codex 0.160.1,
independently of Canvas's bundled ACP 1.10.0. Playwright remains pinned at 0.0.80.
Updating Canvas does not implicitly change these separately selected dependencies.
SDK 1.53's version diagnostic still compares against its bundled ACP 1.10.0 and
logs a mismatch for this explicit override. The runtime installs the locked
adapter during the image build; the warning does not indicate a runtime download.

The [regression suite and native probes](../tests/README.md) check the installed
runtime. Source matches establish patch applicability, not behavioral correctness.
SDK 1.53 replaces mutable sub-agent/model-switch flags with explicit tool lists.
The native factory adapter now filters model switching through that API and keeps
reviewers restricted to the factory reader. No product requirements change.
