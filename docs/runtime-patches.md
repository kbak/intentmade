# Runtime patch review

The runtime pins Canvas 1.25.0 by image digest, with OpenHands SDK/Workspace/Agent
Server 1.53.0 and Automation 1.19.0. Review patches against the installed source
when updating that pin; version guards and exact source matches deliberately
stop the build on drift.

| Integration | Decision for Canvas 1.25.0 | Reason |
| --- | --- | --- |
| Unicode automation downloads | Removed | Automation now sanitizes names, supplies an ASCII fallback and encodes Unicode with `filename*`. The regression exercises the installed upstream route, including compatibility characters. |
| Review permissions and project trust | Retained | SDK's ACP bridge still auto-approves permission requests. The pinned ACP read-only mode still permits workspace writes and trusts project roots. Factory reviews require no writes or escalation, and repository configuration must not replace explicit factory MCP configuration. |
| Startup failures | Retained | SDK still classifies a ChatGPT authentication timeout as rejected credentials and sets terminal error state before emitting its reason. ACP still starts interactive login unless the factory's headless check rejects it. |
| Native agent plugin isolation | Retained | SDK still loads user and project plugins for local conversations. Factory workers and report readers require only explicitly selected tools. |
| Workspace runtime | Retained | DockerWorkspace still needs the factory seccomp profile; ACP session entry points still need factory path mapping and explicit session configuration. |
| Specialist review evidence | Retained | The pinned ACP prompt lifecycle does not emit the factory's role-bound final review receipt. |
| ACP usage accounting | Retained | The pinned ACP still reports retained session usage; factory measurement needs per-prompt deltas and explicit unavailable/reset evidence. |
| Automation outcomes | Retained | Callback schema still accepts only COMPLETED/FAILED, drops task outcome metadata and cannot associate a conversation with phase updates. Native SKIPPED rendering alone does not supply that callback contract. |
| PR archive bounds | Retained | This patches the separately pinned Extensions downloader, which still reads the response and archive member list without the factory's resource bounds. The Canvas update does not change its pinned source. |
| Python Agent Server launcher | Retained | Canvas still invokes `openhands-agent-server`; the factory wrapper ensures both controller and workers execute the patched Python packages and register the read-only report tool. |
| Canvas reply hook | Retained | The upstream entrypoint imports `canvas_ui_tool`; the factory additionally needs its existing reply dispatcher. |

Codex ACP remains pinned at 1.10.0 in both this Canvas release and the factory's
npm lock. Codex and Playwright remain controlled by that lock; updating Canvas
does not implicitly change those separately selected dependencies.

The [regression suite and native probes](../tests/README.md) check the installed
runtime. Source matches establish patch applicability, not behavioral correctness.
SDK 1.53 replaces mutable sub-agent/model-switch flags with explicit tool lists.
The native factory adapter now filters model switching through that API and keeps
reviewers restricted to the factory reader. No product requirements change.
