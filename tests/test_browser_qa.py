"""Native MCP serialization, retained image evidence, and mandatory QA verdicts."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent
import browser_qa
import common
import reporting
import run
from openhands.sdk import Message
from openhands.sdk.agent import ACPAgent
from openhands.sdk.agent.acp_agent import _mcp_config_to_acp_servers
from openhands.sdk.workspace import LocalWorkspace
from PIL import Image
from pydantic import ValidationError


class BrowserQATests(unittest.TestCase):
    def test_native_acp_roundtrip_retains_browser_config_only_when_requested(self):
        with patch.dict(os.environ, FACTORY_CODEX_MODEL="test/high"):
            configured = agent.worker_agent(
                "agent-full-access",
                "factory-browser-qa",
                browser_qa.mcp_config(Path("/tmp/captures")),
            )
            restored = ACPAgent.model_validate_json(configured.model_dump_json())
            servers = _mcp_config_to_acp_servers(
                restored.mcp_config, SimpleNamespace(http=False, sse=False)
            )
            self.assertEqual(servers[0].command, "/acp-node/bin/node")
            self.assertIn("/tmp/captures", servers[0].args)
            self.assertIn("factory-browser-qa", restored.agent_context.to_acp_prompt_context())
            self.assertFalse(agent.worker_agent("read-only", "factory-review").mcp_config)

    def test_pass_requires_actual_checks_and_named_evidence(self):
        for body in (
            {"status": "PASS", "summary": "Done"},
            {
                "status": "PASS",
                "summary": "Done",
                "checks": [{"name": "login", "status": "BLOCKED", "observed": "No account"}],
            },
            {"status": "FAIL", "summary": "Could not start"},
        ):
            with self.assertRaises(ValidationError):
                browser_qa.BrowserResult.model_validate(body)
        with self.assertRaises(ValidationError):
            browser_qa.Screenshot(filename="../secret.png", caption="x", url="x")

    def test_native_file_transfer_and_image_message_survive_source_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "worker"
            output.mkdir()
            Image.new("RGB", (32, 32), "red").save(output / "screen.png")
            evidence = browser_qa.retain(
                LocalWorkspace(working_dir=str(output)),
                output,
                root / "retained",
                [
                    browser_qa.Screenshot(
                        filename="screen.png", caption="Observed error", url="http://localhost"
                    )
                ],
            )
            (output / "screen.png").unlink()
            output.rmdir()
            result = {
                "example": {
                    "status": "PASS",
                    "commit": "tested-sha",
                    "summary": "Verified",
                    "screenshots": evidence,
                }
            }
            with (
                patch.object(reporting, "ACTIVE", {"conversation_id": "report"}),
                patch.object(reporting, "api") as api,
            ):
                reporting.browser_evidence(result)
                body = api.call_args.kwargs["json"]
                message = Message.model_validate({"role": body["role"], "content": body["content"]})
                self.assertTrue(
                    message.content[-1].image_urls[0].startswith("data:image/png;base64,")
                )
                self.assertFalse(body["run"])
            (root / "fake.png").write_text("not an image")
            with self.assertRaises(Exception):
                browser_qa.retain(
                    LocalWorkspace(working_dir=str(root)),
                    root,
                    root / "bad",
                    [
                        browser_qa.Screenshot(
                            filename="fake.png", caption="Fake", url="http://localhost"
                        )
                    ],
                )
            self.assertFalse((root / "bad/1.png").exists())

    def test_qa_runs_on_retained_commit_and_rejects_source_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkout, captures = root / "repo", root / "captures"
            checkout.mkdir()
            captures.mkdir()
            common.git(["init", "-b", "main", str(checkout)])
            common.git(["config", "user.email", "qa@example.test"], cwd=checkout)
            common.git(["config", "user.name", "QA"], cwd=checkout)
            (checkout / "app.html").write_text("Original")
            common.git(["add", "app.html"], cwd=checkout)
            common.git(["commit", "-m", "Initial"], cwd=checkout)
            commit = common.git(["rev-parse", "HEAD"], cwd=checkout).stdout.strip()
            Image.new("RGB", (32, 32), "red").save(captures / "screen.png")
            response = browser_qa.BrowserResult(
                status="PASS",
                summary="Screen verified",
                checks=[
                    browser_qa.BrowserCheck(
                        name="Screen", status="PASS", observed="Original screen visible"
                    )
                ],
                screenshots=[
                    browser_qa.Screenshot(
                        filename="screen.png", caption="Screen", url="http://localhost"
                    )
                ],
            )
            config = {"browser_qa": {"start_command": "true", "url": "http://localhost"}}
            state = {"source": str(checkout), "commit": commit, "base": commit}
            for mutation in (False, True):

                def verify(*args, **kwargs):
                    if mutation:
                        (checkout / "app.html").write_text("Changed during QA")
                    return response

                with patch.object(browser_qa, "converse", side_effect=verify):
                    result = browser_qa.run(
                        LocalWorkspace(working_dir=str(checkout)),
                        config,
                        state,
                        "Verify",
                        captures,
                        root / str(mutation),
                    )
                self.assertEqual(result["status"], "BLOCKED" if mutation else "PASS")
                self.assertEqual(result["commit"], commit)

    def test_qa_failure_uses_existing_repair_loop_and_blocked_qa_never_publishes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = {
                "project": "example",
                "repository": "org/repo",
                "repair_attempts": 1,
                "publish_draft": True,
            }
            states = {
                "example": {
                    "base": "base",
                    "commit": "head",
                    "repository": "/retained",
                    "branch": "task",
                }
            }
            for status in ("FAIL", "BLOCKED"):
                failing = {"example": {"status": status, "summary": "Button does not work"}}
                with (
                    patch.object(
                        run, "implementation_attempt", return_value=({"example": 0}, [])
                    ) as implement,
                    patch.object(
                        run,
                        "browser_checks",
                        side_effect=[failing, {"example": {"status": "PASS", "summary": "Fixed"}}],
                    ),
                    patch.object(
                        run,
                        "review_changes",
                        return_value=run.ReviewResult(verdict="PASS", summary="No code findings"),
                    ),
                    patch.object(run, "publish", return_value="https://example.test/pr") as publish,
                ):
                    if status == "BLOCKED":
                        with self.assertRaises(reporting.NeedsInput):
                            run.execute_build(
                                [config], "task", "Approved spec", "", None, True, root, states
                            )
                        publish.assert_not_called()
                        self.assertEqual(implement.call_count, 1)
                        self.assertEqual(
                            json.loads((root / "result.json").read_text())["status"], "NEEDS_INPUT"
                        )
                    else:
                        run.execute_build(
                            [config], "task", "Approved spec", "", None, True, root, states
                        )
                        self.assertEqual(implement.call_count, 2)
                        self.assertIn("Button does not work", implement.call_args.args[3])
                        publish.assert_called_once()
