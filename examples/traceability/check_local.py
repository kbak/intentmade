"""Run the shared checker through an OpenHands LocalWorkspace."""

import argparse
from pathlib import Path

from openhands.sdk.workspace import LocalWorkspace
from traceability.openhands import check

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--scope", type=Path, required=True)
parser.add_argument("--base", required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
result = check(
    LocalWorkspace(working_dir=str(args.repo.resolve())),
    repo=args.repo.resolve(),
    scope=args.scope.resolve(),
    base=args.base,
    out=args.out.resolve(),
)
print(result.stdout)
if result.stderr:
    print(result.stderr)
raise SystemExit(result.exit_code)
