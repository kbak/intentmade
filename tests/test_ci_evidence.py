"""CI repair sees failure details without relying on worker GitHub access."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_evidence
import followup
import httpx


class CIEvidenceTests(unittest.TestCase):
    def test_annotations_outage_does_not_hide_logs_or_the_first_failed_check(self):
        checks = [
            {
                "id": i,
                "name": f"job-{i}",
                "conclusion": "failure",
                "details_url": f"https://github.com/org/repo/actions/runs/10/job/{i}",
            }
            for i in range(1, 5)
        ]
        response = httpx.Response(403, request=httpx.Request("GET", "https://api.github.com"))
        error = httpx.HTTPStatusError(
            "private signed URL", request=response.request, response=response
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(followup, "latest_check_runs", return_value=checks),
            patch.object(followup.issues, "_github_paginate", side_effect=error),
            patch.object(
                ci_evidence,
                "job_log",
                side_effect=lambda repo, job, token: (
                    f"2026-10-21T03:47:08.0545464Z FAILED test_{job}: {token}\n"
                    + "cleanup output\n" * 10000
                ),
            ) as logs,
        ):
            root = Path(directory)
            context = followup.failure_context(
                {"repository": "org/repo"},
                {"head": {"sha": "head"}, "merge_commit_sha": "merge"},
                "fixture-secret",
                root,
            )
            # [utest~im-ci-evidence~1->req~im-feedback-lifecycle~1]
            self.assertEqual(logs.call_count, 4)
            self.assertLess(len(context), 61000)
            self.assertNotIn("fixture-secret", context)
            self.assertNotIn("private signed URL", context)
            for i in range(1, 5):
                self.assertIn(f"FAILED test_{i}", context)
                retained = (root / f"check-{i}.log").read_text()
                self.assertGreater(len(retained), 100000)
                self.assertIn("HTTP 403", retained)
                self.assertNotIn("fixture-secret", retained)
            worker = root / "worker"
            worker.mkdir()
            staged = ci_evidence.stage(root, worker)
            self.assertIn(str(worker / "ci-evidence/check-1.log"), staged)
            self.assertIn("FAILED test_1", staged)
            self.assertEqual(
                (worker / "ci-evidence/check-1.log").read_bytes(),
                (root / "check-1.log").read_bytes(),
            )

    def test_cve_rows_survive_verbose_cleanup(self):
        row = "| High | CVE-2026-12345 | example-package | 1.0 -> 1.1 |"
        text = "2026-10-21T03:47:08.123Z " + row + "\n" + "cleanup\n" * 10000
        self.assertIn(row, ci_evidence.summary(text, 4000))
        self.assertNotIn("2026-10-21T", ci_evidence.summary(text, 4000))

    def test_signed_log_redirects_never_receive_github_credentials(self):
        calls = []

        def respond(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(
                    302, headers={"location": "https://storage.example/log?sig=private"}
                )
            return httpx.Response(200, content=b"failure details")

        with (
            httpx.Client(transport=httpx.MockTransport(respond)) as client,
            patch.object(ci_evidence.httpx, "stream", client.stream),
        ):
            self.assertEqual(
                ci_evidence.job_log("org/repo", "1", "fixture-secret"), "failure details"
            )
        self.assertEqual(calls[0].headers["Authorization"], "Bearer fixture-secret")
        self.assertNotIn("Authorization", calls[1].headers)

    def test_log_size_and_redirects_are_bounded_and_missing_logs_remain_visible(self):
        with (
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=b"x" * 100)
                )
            ) as client,
            patch.object(ci_evidence.httpx, "stream", client.stream),
            patch.object(ci_evidence, "MAX_LOG_BYTES", 10),
        ):
            log = ci_evidence.job_log("org/repo", "1", "fixture-secret")
            self.assertTrue(log.startswith("x" * 10 + "\n"))
            self.assertIn("truncated", log)
        with (
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        302, headers={"location": "http://storage.example/log"}
                    )
                )
            ) as client,
            patch.object(ci_evidence.httpx, "stream", client.stream),
        ):
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                ci_evidence.job_log("org/repo", "1", "fixture-secret")
        message = ci_evidence.unavailable("Job log", RuntimeError("secret URL"))
        self.assertIn("Job log unavailable: RuntimeError", message)
        self.assertNotIn("secret URL", message)
