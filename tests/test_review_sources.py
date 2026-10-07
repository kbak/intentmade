"""Reference snapshots stay pinned and within operator registrations."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import review_sources


class ReferenceSourcesTests(unittest.TestCase):
    def test_reference_archive_and_metadata_use_captured_commit(self):
        sha = "a" * 40
        registered = {"dependency": {"repository": "org/dependency", "branch": "release/main"}}
        config = {"project": "app", "review_repositories": ["dependency"]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "download"
            archive.mkdir()
            (archive / "profile.json").write_text('{"source": true}')
            with (
                patch.object(review_sources, "projects", return_value=registered),
                patch.object(review_sources, "github", return_value={"sha": sha}) as github,
                patch.object(
                    review_sources.reviews, "_prepare_repository", return_value=archive
                ) as download,
                patch.object(
                    review_sources.issues,
                    "_github_paginate",
                    return_value=[
                        {"number": 2, "merged_at": "2026-10-05T15:32:05Z", "merge_commit_sha": sha}
                    ],
                ) as pulls,
            ):
                result = review_sources.prepare(config, "controller-only", root)
            # [utest~im-review-reference-commit~1->req~im-immutable-pr-comparison~1]
            github.assert_called_once_with(
                "controller-only", "GET", "/repos/org/dependency/commits/release%2Fmain"
            )
            download.assert_called_once_with("controller-only", "org/dependency", 0, sha)
            pulls.assert_called_once_with(
                "controller-only", f"/repos/org/dependency/commits/{sha}/pulls"
            )
            self.assertEqual(result["dependency"]["commit"], sha)
            self.assertEqual(
                (Path(result["dependency"]["source"]) / "profile.json").read_text(),
                '{"source": true}',
            )
            self.assertEqual(
                result["dependency"]["associated_pull_requests"][0]["merged_at"],
                "2026-10-05T15:32:05Z",
            )
            self.assertNotIn("controller-only", str(result))

    def test_invalid_registration_or_unavailable_source_stops_preparation(self):
        registered = {
            "app": {"repository": "org/app"},
            "fixture": {"repository": ""},
            "dependency": {"repository": "org/dep", "branch": "main"},
        }
        for names in (
            "dependency",
            ["unknown"],
            ["app"],
            ["fixture"],
            ["dependency", "dependency"],
            [{}],
        ):
            with self.subTest(names=names), self.assertRaises(ValueError):
                review_sources.validate(
                    {"project": "app", "review_repositories": names}, registered
                )
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(review_sources, "projects", return_value=registered),
            patch.object(review_sources, "github", side_effect=RuntimeError("unavailable")),
            patch.object(review_sources.reviews, "_prepare_repository") as download,
        ):
            # [utest~im-review-reference-unavailable~1->req~im-immutable-pr-comparison~1]
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                review_sources.prepare(
                    {"project": "app", "review_repositories": ["dependency"]},
                    "credential",
                    Path(tmp),
                )
            download.assert_not_called()

    def test_no_references_does_not_fetch_or_reload_configuration(self):
        with patch.object(review_sources, "projects") as projects:
            self.assertEqual(review_sources.prepare({}, "credential", Path("/unused")), {})
            projects.assert_not_called()
