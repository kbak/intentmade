"""CI selection uses complete immutable paths and keeps reported failures blocking."""

import unittest
from unittest.mock import patch

import policy


class PathChecksTests(unittest.TestCase):
    def test_path_override_keeps_ci_fail_closed(self):
        # [utest~im-ci-paths~1->req~im-pr-publication~1]
        config = {
            "repository": "example/repo",
            "required_checks": ["backend"],
            "accepted_check_results": ["success", "neutral", "skipped"],
            "required_check_overrides": [
                {"paths": ["mobile/ios/**"], "required_checks": ["swift", "js"]}
            ],
        }
        pr = {"state": "open", "draft": False, "head": {"sha": "head"}}
        cases = [
            ([{"filename": "mobile/ios/app.js"}], {"swift": "success", "js": "success"}, True),
            ([{"filename": "mobile/ios/app.js"}], {"swift": "success"}, False),
            ([{"filename": "mobile/ios/app.js"}], {"swift": "success", "js": "pending"}, False),
            (
                [{"filename": "mobile/ios/app.js"}],
                {"swift": "success", "js": "success", "security": "failure"},
                False,
            ),
            (
                [{"filename": "mobile/ios/app.js"}, {"filename": "backend/app.py"}],
                {"swift": "success", "js": "success"},
                False,
            ),
            (
                [
                    {
                        "filename": "mobile/ios/app.js",
                        "status": "renamed",
                        "previous_filename": "backend/app.py",
                    }
                ],
                {"swift": "success", "js": "success"},
                False,
            ),
            ([], {"swift": "success", "js": "success"}, False),
        ]
        for files, checks, expected in cases:
            with (
                self.subTest(files=files, checks=checks),
                patch.object(policy, "comparison_files", return_value=files),
                patch.object(policy, "checks_for", return_value=checks),
            ):
                self.assertEqual(policy.pr_eligible("token", pr, config), expected)
        with patch.object(
            policy, "comparison_files", side_effect=ValueError("incomplete inventory")
        ):
            with self.assertRaisesRegex(ValueError, "incomplete inventory"):
                policy.pr_eligible("token", pr, config)
        for rules in (
            {},
            [None],
            [{"paths": [], "required_checks": ["swift"]}],
            [{"paths": ["mobile/**"], "required_checks": []}],
        ):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                policy.validate_check_overrides({"required_check_overrides": rules})

    def test_overlapping_rules_require_union(self):
        config = {
            "required_checks": ["backend"],
            "required_check_overrides": [
                {"paths": ["mobile/**"], "required_checks": ["js"]},
                {"paths": ["mobile/ios/**"], "required_checks": ["swift"]},
            ],
        }
        with patch.object(
            policy, "comparison_files", return_value=[{"filename": "mobile/ios/app.js"}]
        ):
            self.assertEqual(policy.required_checks_for("token", {}, config), ["js", "swift"])
