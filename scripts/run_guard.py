"""Check native run admission immediately before stopping a controller."""

CHECK = """
import sys
sys.path.insert(0, '/opt/factory')
import configure
active = []
for automation in configure.records():
    offset = 0
    while True:
        runs = configure.api('GET', '/api/automation/v1/' + automation['id'] + '/runs',
                             params={'limit': 100, 'offset': offset})['runs']
        active.extend((automation['name'], run['id']) for run in runs
                      if run['status'] in {'PENDING', 'RUNNING'})
        if len(runs) < 100:
            break
        offset += len(runs)
if active:
    print('Factory has active runs; pause admission and drain or reconcile them before restarting:',
          *[name + ' (' + run + ')' for name, run in active], sep='\\n', file=sys.stderr)
    raise SystemExit(1)
"""


# [impl->req~im-interrupted-run~1]
def require_idle(run):
    result = run(
        "docker",
        "compose",
        "ps",
        "--status",
        "running",
        "--quiet",
        "canvas",
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        # Reuse native API authentication in the controller, including older
        # releases. An API/read failure blocks shutdown rather than implying idle.
        run("docker", "compose", "exec", "-T", "canvas", "python", "-c", CHECK)
