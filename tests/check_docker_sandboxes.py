"""Disposable Kit adapter check. Uses native host login; model calls are opt-in."""

import argparse
import ast
import json
import platform
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

import docker_sandboxes
import httpx
import sandbox
from agent import converse
from transfer import export_task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True)
    parser.add_argument("--command", default="sbx")
    parser.add_argument("--publish-host", default="127.0.0.1")
    parser.add_argument("--workspaces", required=True, type=Path)
    parser.add_argument("--native-probes", action="store_true")
    parser.add_argument("--model", help="Opt in to a real ACP call, e.g. gpt-6-astra/low")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(dir=args.workspaces, prefix="kit-check-") as temporary:
        base = Path(temporary)
        root = base / "job"
        source = root / "source"
        source.mkdir(parents=True)
        shutil.copytree(Path(__file__).parent / "fixtures/smoke", source, dirs_exist_ok=True)
        profiles = base / "profiles"
        (profiles / "smoke").mkdir(parents=True)
        (profiles / "smoke/marker").write_text("trusted-profile")
        inputs = base / "inputs"
        inputs.mkdir()
        (inputs / "marker").write_text("trusted-input")
        options = docker_sandboxes.validate(
            {
                "backend": "docker-sandboxes",
                "kit": args.kit,
                "command": args.command,
                "publish_host": args.publish_host,
                "profiles": str(profiles),
            }
        )
        with (
            patch.object(
                sandbox,
                "api",
                return_value={
                    "profile": {
                        "agent_kind": "acp",
                        "acp_server": "codex",
                        "acp_model": args.model,
                    }
                },
            ),
            patch.object(
                sandbox, "FileSecretsStore", side_effect=AssertionError("Canvas secret read")
            ),
            patch.object(sandbox, "Cipher", side_effect=AssertionError("Canvas encryption read")),
            patch.object(docker_sandboxes, "settings", return_value=options),
            patch.object(sandbox.input_artifacts, "directory", return_value=str(inputs)),
            patch.object(sandbox.input_artifacts, "CURRENT", Mock(get=Mock(return_value=True))),
            sandbox.worker(root, {"test_profile": "smoke"}) as workspace,
        ):

            def execute(command, expected=0):
                result = workspace.execute_command(command, cwd=str(source), timeout=60)
                assert result.exit_code == expected, result
                return result.stdout

            assert execute("uname -r").strip() != platform.release()
            execute("test ! -e /run/sbx/sandboxd.sock")
            assert workspace.client.get("/api/settings/secrets/CODEX_AUTH_JSON").status_code == 404
            execute(
                "python -c "
                + shlex.quote(
                    "import json, os; from pathlib import Path; "
                    "assert json.loads((Path(os.environ['CODEX_HOME']) / 'auth.json').read_text()) "
                    "== {'OPENAI_API_KEY': 'proxy-managed'}"
                )
            )
            if args.model:
                reply = converse(workspace, "Reply exactly NATIVE_PROXY_OK. Do not use tools.")
                assert "NATIVE_PROXY_OK" in reply, "Native proxy ACP call failed"
                assert (
                    workspace.client.get("/api/settings/secrets/CODEX_AUTH_JSON").status_code == 404
                )
                print("PASS native proxy ACP call without a Canvas credential", flush=True)
            rejected = httpx.get(
                workspace.host + "/api/settings/secrets/CODEX_AUTH_JSON",
                headers={"X-Session-API-Key": "wrong-fixture-key"},
                timeout=5,
            )
            assert rejected.status_code == 401
            # Reuse the existing kernel assertions, changing only its fixture path.
            tree = ast.parse((Path(__file__).parent / "check_review_sandbox.py").read_text())
            probe = next(
                ast.literal_eval(node.value)
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "PROBE" for t in node.targets)
            )
            probe_path = root / "permission_probe.py"
            probe_path.write_text(
                probe.replace(
                    "/home/openhands/factory-sandbox-probe", "/home/agent/factory-sandbox-probe"
                )
            )
            execute("python " + shlex.quote(str(probe_path)))
            if args.native_probes:
                for filename in (
                    "check_project_config.py",
                    "check_specialist_review.py",
                    "check_agent_startup.py",
                    "check_browser_evidence.py",
                ):
                    target = root / filename
                    shutil.copyfile(Path(__file__).parent / filename, target)
                    execute("python " + shlex.quote(str(target)))
                    print("PASS native " + filename, flush=True)
            execute("python -m unittest discover -s tests", expected=1)
            execute(
                "python -c "
                + shlex.quote(
                    "from pathlib import Path; p=Path('greeting.py'); "
                    "p.write_text(p.read_text().replace('{name}', '{name.strip()}'))"
                )
            )
            execute("python -m unittest discover -s tests")
            assert "name.strip()" in (source / "greeting.py").read_text()
            (source / "controller-marker").write_text("visible")
            assert execute("cat controller-marker") == "visible"
            for path, expected in [
                ("/factory-tests/smoke/marker", "trusted-profile"),
                ("/factory-inputs/marker", "trusted-input"),
            ]:
                assert execute("cat " + path) == expected
                execute("sh -c " + shlex.quote("printf changed > " + path), expected=2)
            execute(
                "git init -b main && git config user.email fixture@example.test && "
                "git config user.name Fixture && git add . && git commit -m base"
            )
            base_commit = execute("git rev-parse HEAD").strip()
            execute("printf 'exported\\n' > export.txt")
            bundle = root / "task.bundle"
            export_task(
                workspace,
                {"worktree": str(source), "branch": "main", "base": base_commit},
                bundle,
                "kit-fixture",
            )
            assert bundle.is_file()
            # No registry request: build FROM scratch with the local echo binary/libraries.
            execute(
                "mkdir -p /tmp/kit-docker/root && cp -L --parents /bin/echo "
                "/lib/x86_64-linux-gnu/libc.so.6 /lib64/ld-linux-x86-64.so.2 /tmp/kit-docker/root && "
                "printf 'FROM scratch\\nCOPY root/ /\\n' > /tmp/kit-docker/Dockerfile && "
                "docker build --network none -t kit-fixture /tmp/kit-docker && "
                "docker run --rm --network none --memory 64m --pids-limit 64 --cpus .5 "
                "kit-fixture /bin/echo nested-docker-ok"
            )
            # A live foreground session must prevent the native 30s idle stop.
            time.sleep(35)
            assert execute("printf session-still-active") == "session-still-active"
            print(
                "PASS native Kit, worker secrets, shared edits, read-only inputs/profiles, "
                "Codex kernel permissions, tests, Git bundle export, nested Docker and active worker session",
                flush=True,
            )
        checkout = base / "imported"
        subprocess.run(
            ["git", "clone", "--branch", "main", str(bundle), str(checkout)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert (checkout / "export.txt").read_text() == "exported\n"
        print("PASS bundle retained after VM teardown; Canvas login never accessed", flush=True)
        manifest = json.loads((root / ".factory-execution.json").read_text())
        assert manifest["intended"]["worker_runtime"]["backend"] == "docker-sandboxes"
        assert manifest["observed"]["worker_image"]["image_id"].startswith("sha256:")


if __name__ == "__main__":
    main()
