"""Document existing requirements and links using an installed Codex ACP agent.

Uses the existing ACP login. Preserves original source and drafts in a clean checkout.
Use --isolated to draft separately. Neither mode accepts or commits a baseline.
"""

import argparse
from pathlib import Path
from uuid import uuid4

from intentbond.recovery import active_recovery, recovery_storage
from openhands.sdk import Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.workspace import LocalWorkspace
from traceability.openhands import check_recovery, prepare_recovery, with_recovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument(
        "--out",
        type=Path,
        help="New directory for originals and check results (default: Git metadata in either mode)",
    )
    parser.add_argument("--focus", required=True, help="Feature or subsystem to document")
    parser.add_argument("--candidate", default="HEAD")
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--input", action="append", dest="inputs")
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    out = args.out.resolve() if args.out else None
    bundle = result_dir = None
    if out is None and args.isolated:
        bundle = recovery_storage(repo) / "runs" / uuid4().hex
    if out is not None:
        if out.is_relative_to(repo):
            parser.error("--out must be outside the source repository")
        out.mkdir(parents=True, exist_ok=False)
        bundle, result_dir = out / "recovery", out / "result"
    source_workspace = LocalWorkspace(working_dir=str(repo))
    prepared = prepare_recovery(
        source_workspace,
        repo=repo,
        out=bundle,
        candidate=args.candidate,
        inputs=args.inputs,
        isolated=args.isolated,
    )
    print(prepared.stdout)
    if prepared.exit_code:
        print(prepared.stderr)
        return prepared.exit_code
    if bundle is None:
        bundle = active_recovery(repo)
    # Isolated authors need access to both draft/ and claims.json. In-place
    # authors work in the actual checkout, with evidence retained in the bundle.
    workspace = LocalWorkspace(working_dir=str(bundle if args.isolated else repo))
    agent = ACPAgent(acp_command=["codex-acp"], agent_context=with_recovery())
    conversation = Conversation(agent=agent, workspace=workspace)
    try:
        conversation.send_message(
            f"Document the existing requirements and code/test links for this scope: {args.focus}\n"
            f"Original source and citation records: {bundle}\nWorking repository: {bundle / 'draft' if args.isolated else repo}\n"
            f"Read {bundle / 'recovery.json'} and {bundle / 'instructions.md'} for the mode and artifact locations. "
            "Read the retained inventory and original source; create native OFT requirements, source/test "
            "coverage comments, scope.json, and claims.json according to the recovery skill. "
            "Include existing properties, candidate invariants, and prioritized missing checks "
            "in the feature table or claim notes for test improvements after requirements review. "
            "Follow the shared skill's documentation-editing and review rules. "
            "Preserve implementation behavior and test assertions. Record contradictions and missing "
            "evidence. Prepare the complete documentation proposal and leave acceptance to review. "
            "Use recover-check --preflight for feedback; the caller will run the full check after your turn. "
            "Preserve the original source snapshot. "
            "Leave the proposal uncommitted and pending review; do not publish it or manufacture approval."
        )
        conversation.run()
        if conversation.state.execution_status.value != "finished":
            print(
                "The documentation task did not finish; inspect the retained draft before checking."
            )
            return 2
    finally:
        conversation.close()
    checked = check_recovery(workspace, recovery=bundle, out=result_dir)
    print(checked.stdout)
    if checked.stderr:
        print(checked.stderr)
    print("Exit 4 leaves the proposal pending review.")
    return checked.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
