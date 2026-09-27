# OpenHands traceability helpers

[`traceability.openhands`](../workflows/traceability/openhands.py) connects
IntentBond to OpenHands agent context and workspaces. It ships with the factory.
Use these helpers for custom tasks; the [factory workflow](traceability.md)
already calls them during implementation and checking.

The runtime needs IntentBond and its OFT JAR. See
[runtime setup](traceability.md#runtime-setup). When running from a factory
checkout, set `PYTHONPATH=workflows` in the same environment.

## Agent context

| Helper | Guidance attached |
| --- | --- |
| `with_traceability(context=None, provisioned=False)` | Requirements, code/test links, shared result meanings, and property-testing workflow. `provisioned=True` omits tool installation instructions. |
| `with_recovery(context=None)` | Documenting an existing project's requirements, links, and missing tests. |
| `with_property_testing(context=None, framework=None)` | Property testing, optionally with `hypothesis`, `fast-check`, `quickcheck`, or `hegel` guidance. |
| `with_model_checking(context=None, language=None, backend="alloy")` | Formal checks using `alloy`, `z3`, or `chc`, with optional `python` or `daml` guidance. |

Options after `context` are keyword-only. Each helper preserves existing context
and replaces its own skill when called again. Skill references are included as
text so remote workers can read them without access to the caller's files.

Attach context before creating a conversation or worktree:

```python
from openhands.sdk.agent import ACPAgent
from traceability.openhands import with_traceability

agent = ACPAgent(
    acp_command=["codex-acp"],
    agent_context=with_traceability(provisioned=True),
)
```

Supply the approved task, repository, scope, baseline, and output path in the
task message. Resumed conversations retain their saved context; attach the
guidance when creating a fresh repair session.

## Workspace commands

`check(workspace, repo=..., scope=..., base=..., out=...)` checks the workspace's
current worktree. Paths must be absolute in that workspace. Keep the trusted
scope outside the candidate's control, hold the baseline fixed through repairs,
and use a new output directory outside the project for every invocation.

```python
from traceability.openhands import check

result = check(
    workspace,
    repo="/workspaces/project",
    scope="/task-inputs/scope.json",
    base="BASE_COMMIT",
    out="/task-output/check-1",
)
```

The result preserves the command's exit code and output. Exit 4 means automated
checks passed and review is pending. Retain the evidence directory using the
workspace's file transport. The caller handles review and completion.

`prepare_recovery(workspace, repo=...)` captures original source and prepares
records for documenting requirements. Use `isolated=True` for a separate draft.
`check_recovery(workspace, repo=...)` checks an in-place proposal; supply
`recovery=...` instead for a bundle. `preflight=True` checks edits, citations, and
links without running tests. A successful preflight returns exit 5; a full check
returns exit 4 while review is pending.

All command helpers accept `env` and `timeout`. Default timeouts are 600 seconds
for preparation and 5,600 seconds for checks. The execution workspace needs
IntentBond, Git, Java, and the project's test dependencies.

## Examples

- [Run a local check](../examples/traceability/check_local.py).
- [Document existing requirements with an ACP agent](../examples/traceability/recover_local.py).

The recovery example uses the existing ACP login and retains originals and
results under Git metadata unless `--out` is supplied. These records stay local;
retain complete bundles for shared review. Review and commit accepted requirements
using the [IntentBond recovery guide](https://github.com/kbak/intentbond/blob/main/docs/recovery.md).
