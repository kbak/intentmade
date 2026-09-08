# Codex container sandbox

`codex-seccomp.json` starts from Moby's default profile at
[`seccomp/v0.2.2`](https://github.com/moby/profiles/blob/seccomp/v0.2.2/seccomp/default.json).
The upstream file's SHA-256 is
`1a4d017730647621403920d9558aa0152adef04dd89ed7720e4abafa24551999`.
Moby profiles are licensed under Apache-2.0.

The final rule permits `clone`, `unshare`, `mount`, `umount2`, and `pivot_root`
for Codex's unprivileged namespace sandbox. Docker's other syscall restrictions
and default capability set remain in effect. No host namespaces, privileged
Canvas, or `CAP_SYS_ADMIN` are added. Mount operations require capabilities in
the newly created user namespace; the agent has no mount capability in the
container's original namespace.

Canvas selects this profile in Compose. The pinned DockerWorkspace launcher
passes the image's `FACTORY_SECCOMP_PROFILE` to the Docker client for each worker.
The legacy Landlock backend cannot enforce the workspace permission profiles
used by interactive Codex sessions, so all roles use the modern sandbox.

Canvas contains a frozen server executable with an independent SDK copy.
Coordinator context therefore applies in the actual Codex ACP adapter, including
new, resumed, loaded, and forked sessions. The worker container launcher runs in
the installed Python SDK used by the factory workflow.

Repository chats retain the selected catalog in their instructions while Codex
runs from `/projects`. This gives its sandbox a writable coordination directory
for protected-path placeholders and keeps the actual repository catalog read-only.

Run `python3 tests/check_review_sandbox.py` to exercise both workspace-write
and read-only execution against the actual kernel. Revisit the upstream profile
and these probes when updating Docker or Codex.
