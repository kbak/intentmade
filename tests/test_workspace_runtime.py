"""Regressions for selected repository chats and the installed runtime launchers."""

import json
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import factory_context
from openhands.workspace import DockerWorkspace
from openhands.workspace.docker import workspace as docker_module


class CoordinatorContextTests(unittest.TestCase):
    def context(self, cwd="/projects/repos/common-defense-app", coordinator=True):
        script = """
import { factorySessionRequest, factorySessionConfig } from '/opt/factory/factory-context.mjs';
const original = JSON.parse(process.argv[1]);
const request = factorySessionRequest(original);
const config = factorySessionConfig({ existing: true, developer_instructions: 'Keep this guidance' }, request);
console.log(JSON.stringify({cwd: request.cwd, config, original: original.cwd}));
"""
        return json.loads(
            subprocess.check_output(
                [
                    "/acp-node/bin/node",
                    "--input-type=module",
                    "-e",
                    script,
                    json.dumps({"cwd": cwd}),
                ],
                env={"FACTORY_COORDINATOR": "1" if coordinator else ""},
                text=True,
            )
        )

    def test_nested_repository_gets_factory_handoff_and_preserves_config(self):
        result = self.context()
        config = result["config"]
        self.assertTrue(config["existing"])
        instructions = config["developer_instructions"]
        self.assertTrue(instructions.startswith("Keep this guidance"))
        self.assertIn('Selected factory project: "common-defense-app"', instructions)
        self.assertIn("/opt/factory/configure.py submit", instructions)
        self.assertIn("read-only catalog", instructions)
        self.assertIn("Do not implement in the catalog or in a temporary copy", instructions)
        self.assertEqual(result["cwd"], "/projects")
        self.assertEqual(result["original"], "/projects/repos/common-defense-app")

    def test_selected_catalog_offers_a_lookup_for_its_repository(self):
        for directory in ("/projects/repos/pilot", "/projects/repos/pilot/docs/design"):
            with self.subTest(directory=directory):
                instructions = self.context(cwd=directory)["config"]["developer_instructions"]
                commands = [
                    shlex.split(line)
                    for line in instructions.splitlines()
                    if line.startswith("python /opt/factory/configure.py discussion ")
                ]
                self.assertEqual(
                    commands, [["python", "/opt/factory/configure.py", "discussion", "pilot"]]
                )

    def test_workers_and_unrelated_workspaces_keep_their_own_context(self):
        for options in [
            {"coordinator": False},
            {"cwd": "/workspaces/job/source"},
            {"cwd": "/projects/repos-unrelated/project"},
        ]:
            with self.subTest(options=options):
                result = self.context(**options)
                self.assertEqual(result["config"]["developer_instructions"], "Keep this guidance")
                self.assertEqual(result["cwd"], result["original"])

    def test_catalog_trust_is_exact_idempotent_and_keeps_existing_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "catalog"
            repo = catalog / "example"
            (repo / ".git").mkdir(parents=True)
            (catalog / "not-a-repo").mkdir()
            (catalog / "external-link").symlink_to(repo, target_is_directory=True)
            env = {"GIT_CONFIG_GLOBAL": str(root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
            with patch.dict(os.environ, env), patch.object(factory_context, "CATALOG", catalog):
                subprocess.run(
                    ["git", "config", "--global", "--add", "safe.directory", "/keep"], check=True
                )
                factory_context.trust_catalog()
                factory_context.trust_catalog()
                result = subprocess.check_output(
                    ["git", "config", "--global", "--get-all", "safe.directory"], text=True
                ).splitlines()
            self.assertEqual(result, ["/keep", str(repo), str(repo / ".git")])

    def test_installed_adapter_sends_context_on_new_resume_and_load(self):
        source = Path(
            "/acp-node/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
        ).read_text()
        methods = []
        for name in ["newSession", "resumeSession", "loadSession"]:
            matches = re.findall(r"  async " + name + r"\(request\b.*?\n  \}", source, re.DOTALL)
            self.assertEqual(len(matches), 1)
            methods.append(matches[0])
        script = """
import { factorySessionRequest, factorySessionConfig } from '/opt/factory/factory-context.mjs';
const readAdditionalDirectories = () => [];
const captures = [];
class Launcher { METHODS }
const launcher = new Launcher();
const capture = async (params) => { captures.push(params); return {thread:{id:'probe'},model:'fixture'}; };
Object.assign(launcher, {
  codexClient: {threadStart:capture, threadResume:capture, threadRead:async()=>({thread:{id:'probe'}})},
  refreshSkills:async()=>{}, createSessionConfig:async()=>({existing:true}),
  getModelProvider:()=>null, getResumeModelProvider:async()=>null,
  fetchAvailableModels:async()=>[{}], createModelId:()=>({toString:()=> 'fixture'}),
  getCollaborationMode:()=>null,
});
for (const name of ['newSession','resumeSession','loadSession']) {
  await launcher[name]({cwd:'/projects/repos/example',sessionId:'probe',mcpServers:[]});
}
console.log(JSON.stringify(captures));
""".replace("METHODS", "\n".join(methods))
        captures = json.loads(
            subprocess.check_output(
                ["/acp-node/bin/node", "--input-type=module", "-e", script],
                env={"FACTORY_COORDINATOR": "1"},
                text=True,
            )
        )
        self.assertEqual(len(captures), 3)
        for call in captures:
            self.assertEqual(call["cwd"], "/projects")
            self.assertTrue(call["config"]["existing"])
            self.assertIn(
                'Selected factory project: "example"', call["config"]["developer_instructions"]
            )

    def test_rpc_boundary_retains_coordinator_cwd_for_subsequent_turns(self):
        source = Path(
            "/acp-node/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
        ).read_text()
        methods = re.findall(r"  async newSession\(params\).*?\n  \}", source, re.DOTALL)
        self.assertEqual(len(methods), 1)
        script = """
import { factorySessionRequest, factorySessionConfig } from '/opt/factory/factory-context.mjs';
const logger = {log:()=>{}};
let captured;
class Server { METHOD }
const server = new Server();
Object.assign(server, {
  providerUpdate:null,
  getOrCreateSession:async request => {captured={cwd:request.cwd,config:factorySessionConfig({},request)};return ['probe',{availableModels:[],currentModelId:'fixture'},{}];},
  getSessionState:()=>({}), createSessionConfigOptionsResponse:()=>({}),
});
await server.newSession({cwd:'/projects/repos/example',mcpServers:[]});
console.log(JSON.stringify(captured));
""".replace("METHOD", methods[0])
        result = json.loads(
            subprocess.check_output(
                ["/acp-node/bin/node", "--input-type=module", "-e", script],
                env={"FACTORY_COORDINATOR": "1"},
                text=True,
            )
        )
        self.assertEqual(result["cwd"], "/projects")
        self.assertIn(
            'Selected factory project: "example"', result["config"]["developer_instructions"]
        )

    def test_native_worker_launcher_passes_seccomp_to_docker(self):
        commands = []

        def execute(command):
            commands.append(command)
            if command[:2] == ["docker", "run"]:
                raise RuntimeError("stop before creating a real container")
            return SimpleNamespace(returncode=0)

        workspace = SimpleNamespace(
            host_port=38123,
            extra_ports=False,
            forward_env=[],
            volumes=[],
            enable_gpu=False,
            network="probe",
            platform="linux/amd64",
        )
        with (
            patch.dict(os.environ, {"FACTORY_SECCOMP_PROFILE": "/opt/factory/codex-seccomp.json"}),
            patch.object(docker_module, "check_port_available", return_value=True),
            patch.object(docker_module, "execute_command", side_effect=execute),
            self.assertRaisesRegex(RuntimeError, "stop before creating"),
        ):
            DockerWorkspace._start_container(workspace, "factory:probe", None)
        command = commands[-1]
        self.assertEqual(
            command[command.index("--security-opt") + 1], "seccomp=/opt/factory/codex-seccomp.json"
        )


if __name__ == "__main__":
    unittest.main()
