"""Tests for the tri-noul Stage 1 gate and Stage 3 shortlist rerank."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tink_route.adapters.client import JevRouterClient
from tink_route.core.exceptions import ApiProtocolError
from tink_route.metadata import load_library_skills, parse_skill_metadata


class TestTriNoulGate(unittest.TestCase):
    def setUp(self) -> None:
        self.client = JevRouterClient(api_key="test-key")

    def test_tri_gate_rejects_explanatory_requests(self) -> None:
        """Orthogonal nouls down-weight subject-matter explanations that need no skill."""
        gate = {
            "answers": {
                "acts_on_user_system": {"noul": 0.05},
                "would_follow_documented_procedure": {"noul": 0.10},
                "prose_suffices": {"noul": 0.95},
            }
        }
        with patch.object(self.client, "_call_api", return_value=gate) as api:
            res = self.client.route(
                "Explain how GLSL vertex shaders calculate lighting",
                [{"name": "threejs-shaders", "description": "Write GLSL shaders"}],
                tri_gate=True, rerank=False,
            )
        self.assertEqual(res.status, "no_skill_needed")
        self.assertAlmostEqual(res.specialist_noul or 0.0, 0.067, places=3)
        self.assertEqual(api.call_count, 1)
        questions = api.call_args.args[0]["questions"]
        self.assertIn("acts_on_user_system", questions)
        self.assertNotIn("specialist_needed", questions)

    def test_tri_gate_missing_answers_are_protocol_errors(self) -> None:
        with patch.object(self.client, "_call_api", return_value={"answers": {"acts_on_user_system": {"noul": 0.9}}}):
            with self.assertRaises(ApiProtocolError):
                self.client.route("Build shaders", [{"name": "threejs-shaders", "description": "GLSL"}], tri_gate=True, rerank=False)


class TestShortlistRerank(unittest.TestCase):
    def setUp(self) -> None:
        self.client = JevRouterClient(api_key="test-key")
        self.skills = [
            {
                "name": "powerpoint",
                "description": "Create, read, edit .pptx decks",
                "description_full": "Create, read, edit .pptx decks",
                "body": "# PowerPoint\nInspect, extract text, and patch existing presentations.",
            },
            {
                "name": "pptx-author",
                "description": "Build PowerPoint decks headless with python-pptx",
                "description_full": "Build PowerPoint decks headless with python-pptx",
                "body": "# PPTX Author\nUse python-pptx to generate slides from scratch.",
            },
        ]
        self.noul = {"answers": {"specialist_needed": {"noul": 0.92}}}
        self.stage2 = {
            "answers": {
                "selected_skill": {
                    "choice": "powerpoint",
                    "confidence": 0.70,
                    "probabilities": {"powerpoint": 0.70, "pptx-author": 0.65},
                }
            }
        }

    def test_rerank_corrects_lookalike_winner(self) -> None:
        rerank = {
            "answers": {
                "selected_skill": {
                    "choice": "pptx-author",
                    "confidence": 0.93,
                    "probabilities": {"pptx-author": 0.93, "powerpoint": 0.07},
                },
                "fits::powerpoint": {"noul": 0.22},
                "fits::pptx-author": {"noul": 0.91},
            }
        }
        with patch.object(self.client, "_call_api", side_effect=[self.noul, self.stage2, rerank]) as api:
            res = self.client.route(
                "Author a new investor pitch deck from scratch",
                self.skills,
                rerank=True, tri_gate=False,
            )
        self.assertEqual(res.status, "routed")
        self.assertEqual(res.winner, "pptx-author")
        self.assertEqual(res.probability, 0.93)
        self.assertEqual(res.fits, {"powerpoint": 0.22, "pptx-author": 0.91})
        self.assertEqual(api.call_count, 3)
        rerank_payload = api.call_args_list[2].args[0]
        criteria = rerank_payload["questions"]["selected_skill"]["criteria"]
        self.assertIn("python-pptx", criteria["pptx-author"])
        self.assertIn("fits::pptx-author", rerank_payload["questions"])

    def test_rerank_rejects_when_no_candidate_fits(self) -> None:
        rerank = {
            "answers": {
                "selected_skill": {
                    "choice": "powerpoint",
                    "confidence": 0.80,
                    "probabilities": {"powerpoint": 0.80, "pptx-author": 0.20},
                },
                "fits::powerpoint": {"noul": 0.12},
                "fits::pptx-author": {"noul": 0.18},
            }
        }
        with patch.object(self.client, "_call_api", side_effect=[self.noul, self.stage2, rerank]):
            res = self.client.route(
                "Post this announcement to Mastodon",
                self.skills,
                rerank=True, tri_gate=False,
            )
        self.assertEqual(res.status, "no_match")
        self.assertIsNone(res.winner)

    def test_rerank_off_does_not_make_a_third_call(self) -> None:
        with patch.object(self.client, "_call_api", side_effect=[self.noul, self.stage2]) as api:
            res = self.client.route("Author a pitch deck", self.skills, rerank=False, tri_gate=False)
        self.assertEqual(res.status, "routed")
        self.assertEqual(res.winner, "powerpoint")
        self.assertEqual(api.call_count, 2)
        self.assertIsNone(res.fits)

    def test_rerank_missing_fits_answer_is_protocol_error(self) -> None:
        rerank = {
            "answers": {
                "selected_skill": {
                    "choice": "pptx-author",
                    "confidence": 0.93,
                    "probabilities": {"pptx-author": 0.93},
                }
            }
        }
        with patch.object(self.client, "_call_api", side_effect=[self.noul, self.stage2, rerank]):
            with self.assertRaises(ApiProtocolError):
                self.client.route("Author a pitch deck", self.skills, rerank=True, tri_gate=False)


class TestMetadataBodyExcerpt(unittest.TestCase):
    def test_parse_skill_metadata_extracts_body(self) -> None:
        raw = """---
name: pptx-author
description: Build decks
---
# PPTX Author
Use python-pptx to generate slides.
"""
        meta = parse_skill_metadata(raw, "fallback")
        self.assertEqual(meta["name"], "pptx-author")
        self.assertIn("python-pptx", meta["body"])

    def test_load_library_skills_includes_truncated_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "pptx-author"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: pptx-author\ndescription: Build decks\n---\n"
                + ("A" * 800)
            )
            skills = load_library_skills(Path(tmpdir))
            self.assertEqual(len(skills), 1)
            self.assertEqual(len(skills[0]["body"]), 700)
            self.assertEqual(skills[0]["description_full"], "Build decks")
