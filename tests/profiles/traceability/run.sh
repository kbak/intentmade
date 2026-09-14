#!/usr/bin/env bash
set -euo pipefail
# Both the agent's documented invocation and the controller must supply these.
test -d "$FACTORY_WORKSPACE/pilot"
test -d "$TMPDIR"
test "$COMPOSE_PROJECT_NAME" = factory-tests-0
test "$FACTORY_TESTS" = /factory-tests/traceability
[[ "$PROJECT_DIR" == "$TMPDIR/"* ]]
test ! -d "$PROJECT_DIR/.git"
python -m unittest discover -s tests -v
