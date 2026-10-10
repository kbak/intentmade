"""Real policy decisions and controller authority for the Cedar authorization."""

import itertools
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import authorization
import deployment


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifact = Path(temporary.name)
        self.configs = [{"project": "app", "repository": "example/app"}]
        self.states = {"app": {"base": "a" * 40, "commit": "b" * 40}}
        self.facts = {
            "tests_passed": True,
            "review_verdict": "PASS",
            "browser_passed": True,
            "traceability_passed": True,
        }

    def authorize(self, attempt=0):
        return authorization.authorize(
            self.configs, "task", attempt, self.artifact, self.states, self.facts
        )

    def receipt(self, attempt=0):
        return json.loads((self.artifact / "authorization" / f"attempt-{attempt}.json").read_text())

    def response(self, allowed=True, **overrides):
        def respond(command, **kwargs):
            request = json.loads(kwargs["input"])
            response = {
                "protocol": 3,
                "request_id": request["request_id"],
                "action": request["action"],
                "engine_version": "4.13.0",
                "policy_sha256": authorization.policy_digest(),
                "allowed": allowed,
                **overrides,
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(response), "")

        return respond

    def test_real_policy_matrix_and_stateless_restarts(self):
        # [utest~im-authorization-matrix~1->req~im-authorization~1,req~im-publication-gate~1,impl~im-authorization-validation-complete~1]
        for attempt, (tests, review, browser, trace) in enumerate(
            itertools.product(
                (False, True),
                ("PASS", "CHANGES_REQUESTED", "BLOCKED"),
                (False, True),
                (False, True),
            )
        ):
            self.facts.update(
                tests_passed=tests,
                review_verdict=review,
                browser_passed=browser,
                traceability_passed=trace,
            )
            allowed = tests and review == "PASS" and browser and trace
            with self.subTest(facts=self.facts):
                self.assertEqual(self.authorize(attempt), allowed)
                receipt = self.receipt(attempt)
                self.assertEqual(receipt["status"], "ALLOW" if allowed else "DENY", receipt)
                self.assertEqual(receipt["decision"]["allowed"], allowed)
                self.assertEqual(
                    (self.artifact / "authorization" / f"attempt-{attempt}.json").stat().st_mode
                    & 0o777,
                    0o600,
                )
                self.assertEqual(
                    receipt["decision"]["policy_sha256"], authorization.policy_digest()
                )
        # Every invocation is a fresh process and retains only its decision receipt.
        self.assertEqual(len(list((self.artifact / "authorization").iterdir())), 24)
        self.assertFalse(list(self.artifact.rglob("*.redb")))

    def test_engine_is_authoritative_without_python_predicate(self):
        # [utest~im-authorization-authority~1->req~im-authorization~1]
        for attempt, engine_allowed in enumerate((False, True)):
            # Opposite evidence would yield the opposite result from the removed conjunction.
            self.facts["tests_passed"] = not engine_allowed
            with patch.object(
                authorization.subprocess, "run", side_effect=self.response(engine_allowed)
            ):
                self.assertEqual(self.authorize(attempt), engine_allowed)
                self.assertEqual(
                    self.receipt(attempt)["status"], "ALLOW" if engine_allowed else "DENY"
                )
                self.assertNotIn("python_allowed", self.receipt(attempt))

    def test_engine_errors_block_progression_without_python_fallback(self):
        # [utest~im-authorization-errors~1->req~im-authorization~1]
        failures = [
            FileNotFoundError("bridge missing"),
            subprocess.TimeoutExpired("bridge", 15),
            subprocess.CalledProcessError(1, "bridge", stderr="private detail"),
            self.response(allowed="true"),
            self.response(request_id="another request"),
            self.response(action="another:action"),
            self.response(policy_sha256="another policy"),
            self.response(engine_version="another engine"),
            self.response(protocol=True),
            subprocess.CompletedProcess("bridge", 0, "invalid JSON", ""),
            subprocess.CompletedProcess("bridge", 0, "[]", ""),
        ]
        for attempt, (failure, allowed) in enumerate(itertools.product(failures, (False, True))):
            self.facts["tests_passed"] = allowed
            replacement = (
                {"side_effect": failure}
                if callable(failure) or isinstance(failure, Exception)
                else {"return_value": failure}
            )
            with (
                self.subTest(failure=failure),
                patch.object(authorization.subprocess, "run", **replacement),
            ):
                with self.assertRaisesRegex(RuntimeError, "progression blocked"):
                    self.authorize(attempt)
                receipt = self.receipt(attempt)
                self.assertEqual(receipt["status"], "ERROR")
                self.assertNotIn("private detail", json.dumps(receipt))

    def test_all_projects_invoke_bridge_without_operator_selection(self):
        # [utest~im-authorization-no-bypass~1->req~im-authorization~1]
        with patch.object(authorization.subprocess, "run", side_effect=self.response()) as bridge:
            for attempt, project in enumerate(("app", "other", "group-member")):
                self.configs = [{"project": project, "repository": "example/" + project}]
                self.states = {project: {"base": "a" * 40, "commit": "b" * 40}}
                self.assertTrue(self.authorize(attempt))
                self.assertEqual(self.receipt(attempt)["status"], "ALLOW")
            self.assertEqual(bridge.call_count, 3)

    def test_enforcement_rejects_missing_facts_and_sources(self):
        # [utest~im-authorization-facts~1->req~im-authorization~1]
        original_facts = self.facts.copy()
        for attempt, key in enumerate(original_facts):
            self.facts = {name: value for name, value in original_facts.items() if name != key}
            with self.subTest(missing=key), self.assertRaisesRegex(RuntimeError, "blocked"):
                self.authorize(attempt)
            self.assertEqual(self.receipt(attempt)["status"], "ERROR")
        self.facts = {**original_facts, "tests_passed": "true"}
        with self.assertRaisesRegex(RuntimeError, "blocked"):
            self.authorize(4)
        self.facts = original_facts
        for attempt, states in enumerate(({}, {"app": {"base": "a" * 40}}), 5):
            self.states = states
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                self.authorize(attempt)
            self.assertEqual(self.receipt(attempt)["status"], "ERROR")

    def test_receipt_failure_blocks_enforcement_even_after_engine_allows(self):
        # [utest~im-authorization-receipt~1->req~im-authorization~1]
        for attempt, allowed in enumerate((False, True)):
            with patch.object(authorization.subprocess, "run", side_effect=self.response(allowed)):
                directory = self.artifact / "authorization"
                directory.mkdir(exist_ok=True)
                path = directory / f"attempt-{attempt}.json"
                path.write_text("retained receipt")
                with self.assertRaisesRegex(RuntimeError, "receipt unavailable"):
                    self.authorize(attempt)
                self.assertEqual(path.read_text(), "retained receipt")

    def test_group_and_candidate_identity_are_recorded_as_one_decision(self):
        # [utest~im-authorization-group~1->req~im-authorization~1]
        self.configs.append({"project": "other", "repository": "example/other"})
        self.states["other"] = {"base": "c" * 40, "commit": "d" * 40}
        with (
            patch.object(authorization.subprocess, "run", side_effect=self.response()),
        ):
            self.authorize(attempt=0)
            self.states["app"]["commit"] = "e" * 40
            self.authorize(attempt=1)
        sources = json.loads(self.receipt()["request"]["source_identity"])
        self.assertEqual(set(sources), {"app", "other"})
        self.assertEqual(sources["other"]["candidate"], "d" * 40)
        self.assertEqual(sources["app"]["repository"], "example/app")
        self.assertNotEqual(
            self.receipt()["request"]["source_identity"],
            self.receipt(1)["request"]["source_identity"],
        )
        # Local fixture builds bind the controller-retained repository instead
        # of requiring a remote repository registration.
        self.configs[0]["repository"] = ""
        self.states["app"]["repository"] = str(self.artifact / "task.git")
        self.authorize(attempt=2)
        sources = json.loads(self.receipt(2)["request"]["source_identity"])
        self.assertEqual(sources["app"]["repository"], self.states["app"]["repository"])

    def test_real_bridge_rejects_malformed_requests(self):
        # [utest~im-authorization-requests~1->req~im-authorization~1]
        request = {
            "protocol": 3,
            "request_id": "fixture",
            "run": "run",
            "task": "task",
            "attempt": 0,
            "source_identity": "{}",
            "action": "validation:complete",
            "facts": self.facts,
        }
        for payload in (
            "{",
            {**request, "facts": {**self.facts, "tests_passed": "true"}},
            {**request, "extra": True},
            {**request, "request_id": ""},
            {**request, "source_identity": ""},
            {**request, "run": ""},
            {**request, "task": ""},
            {**request, "attempt": -1},
            {**request, "attempt": True},
            {**request, "protocol": True},
            {**request, "action": "unknown:action"},
            {**request, "facts": []},
            {**request, "facts": {**self.facts, "task": "spoofed"}},
            {**request, "source_identity": "x" * (128 * 1024)},
        ):
            result = subprocess.run(
                [authorization.BRIDGE],
                input=payload if isinstance(payload, str) else json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_operator_configuration_cannot_enter_repository_or_saved_workflow(self):
        # [utest~im-authorization-configuration~1->req~im-authorization~1]
        for engine, mode in itertools.product(("dogwood", "cedar"), ("off", "shadow", "enforce")):
            options = {engine: {"mode": mode, "projects": ["app"]}}
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, "deployment.json"):
                    deployment.workflow_settings({}, options)
                self.assertNotIn(engine, deployment.workflow_settings(options, {}))
                directory = self.artifact / (engine + mode)
                directory.mkdir()
                (directory / "deployment.json").write_text(json.dumps(options))
                with self.assertRaisesRegex(ValueError, "always enforced"):
                    deployment.settings(directory)
        self.assertNotIn("dogwood", deployment.settings(self.artifact))
