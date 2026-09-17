"""Independent CI failures survive name collisions and genuine retries supersede old runs."""

import unittest
from unittest.mock import patch

import policy


def check(run, job, conclusion="success", provider=5):
    return {
        "id": job,
        "app": {"id": provider, "slug": "github-actions"},
        "name": "unit",
        "status": "completed",
        "conclusion": conclusion,
        "details_url": f"https://github.com/org/repo/actions/runs/{run}/job/{job}",
    }


class CheckIdentityTests(unittest.TestCase):
    def results(self, checks, runs):
        def github(token, method, path, params=None):
            if path.endswith("/check-runs"):
                offset = (params["page"] - 1) * 100
                return {"check_runs": checks[offset : offset + 100]}
            if path.endswith("/status"):
                return {"statuses": []}
            return runs[int(path.rsplit("/", 1)[-1])]

        with patch.object(policy, "github", side_effect=github) as api:
            result = policy.checks_for("secret", "org/repo", "sha")
        return result, api

    def test_latest_run_of_same_workflow_supersedes_failure_across_pages(self):
        checks = [check(30, 300)] + [check(10, i, "failure") for i in range(1, 101)]
        metadata = {"workflow_id": 1, "event": "push", "head_branch": "main"}
        result, api = self.results(checks, {10: metadata, 30: metadata})
        self.assertEqual(result, {"unit": "success"})
        self.assertEqual(sum("/actions/runs/" in call.args[2] for call in api.call_args_list), 2)

    def test_independent_workflows_events_branches_and_providers_keep_failure(self):
        old = {"workflow_id": 1, "event": "push", "head_branch": "main"}
        for change, provider in (
            ({"workflow_id": 2}, 5),
            ({"event": "pull_request"}, 5),
            ({"head_branch": "release"}, 5),
            ({}, 6),
        ):
            with self.subTest(change=change, provider=provider):
                checks = [check(10, 1, "failure"), check(30, 3, provider=provider)]
                result, _ = self.results(checks, {10: old, 30: {**old, **change}})
                self.assertEqual(result, {"unit": "failure"})
                self.assertFalse(
                    policy.checks_pass(
                        result, {"required_checks": ["unit"], "accepted_check_results": ["success"]}
                    )
                )

    def test_duplicate_jobs_in_same_run_keep_failed_or_pending_job(self):
        metadata = {"workflow_id": 1, "event": "push", "head_branch": "main"}
        for failed in (check(10, 1, "failure"), {**check(10, 1), "status": "in_progress"}):
            result, _ = self.results([failed, check(10, 3)], {10: metadata})
            self.assertIn(result["unit"], {"failure", "pending"})

    def test_unknown_retry_relationship_keeps_failure(self):
        for modification in ({"details_url": None}, {"app": {"id": 5}}, {}):
            with self.subTest(modification=modification):
                result, _ = self.results(
                    [{**check(10, 1, "failure"), **modification}, {**check(30, 3), **modification}],
                    {10: {}, 30: {}},
                )
                self.assertEqual(result, {"unit": "failure"})
