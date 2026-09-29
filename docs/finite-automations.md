# Finite operator automations

Use the native finite registration for authorized one-shot recipes. The factory
wrapper reports normal success, nonzero exits, exceptions and timeouts through
the existing OpenHands completion API. The recipe does not implement a callback.
This avoids completed work remaining RUNNING until the native watchdog expires.

Create an explicit request beside the recipe, for example:

```json
{
  "name": "Qualification for accepted release abc123",
  "command": ["python", "qualify.py", "--source", "abc123"],
  "timeout": 1200,
  "files": {"qualify.py": "qualify.py", "inputs/manifest.json": "manifest.json"}
}
```

File sources are relative to the request file (absolute sources also work).
Payload names are normalized relative paths. Uploaded input digests are checked
before execution, and payload files cannot replace the factory lifecycle code.
Commands are argument lists, with no implicit shell. Recipes are trusted operator
code, not a sandbox for arbitrary untrusted scripts. They run in the existing
native automation environment; use the normal isolated worker for product code.

```sh
python /opt/factory/configure.py finite /projects/requests/qualification.json --run
python /opt/factory/configure.py finite-status AUTOMATION_ID RUN_ID
```

Omit `--run` to register without dispatch. The automation has a false-filter
manual trigger, never a polling schedule. Duplicate names are rejected so a lost
registration/dispatch response causes inspection rather than blind re-execution.
Before retrying any uncertain operation, reconcile the native automation/run list.
Registration reads back native uploads and automation changes before issuing
dependent requests. A delayed database commit causes bounded read polling;
creation and dispatch requests are not repeated.

`/projects/artifacts/RUN_ID-finite/result.json` records execution status, exit
code, UTC endpoints, monotonic execution duration and separate callback timing.
Execution results are saved before acknowledgement. The status command reports
a finished-execution/native-RUNNING disagreement even when the acknowledgement
failed. A second wrapper launch with the same run ID refuses to execute again.
This does not make external effects exactly once across separately dispatched runs.

The wrapper kills a timed-out recipe's process group before reporting failure.
An abrupt kill of the wrapper itself can still leave an unfinished receipt and
requires native cancellation/reconciliation. An unacknowledged completed receipt
is diagnostic evidence, not authority to mark an arbitrary task successful.
Retain original failures and use the native completion API only after checking
the expected command, source, artifacts and absence of active execution. Never
repair status by editing native storage or repeating the recipe.

Rebuild the factory image and refresh workflows to make this command and its
instructions available to coordinators. Existing uploaded automations retain
their original entrypoint.
