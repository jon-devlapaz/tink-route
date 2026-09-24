"""Tests for the tri-noul Stage 1 gate and Stage 3 shortlist rerank."""

import unittest
from unittest.mock import patch

from tink_route.adapters.client import JevRouterClient
from tink_route.core.exceptions import ApiProtocolError


class TestTriNoulGate(unittest.TestCase):
    def setUp(self) -> None:
        self.client = JevRouterClient(api_key="test-key")

    def test_tri_gate_rejects_explanatory_requests(self) -> None:
        """A low specialised-workflow score returns no_skill_needed before rerank."""
        gate = {"answers": {"specialised_workflow": {"noul": 0.10}}}
        with patch.object(self.client, "_call_api", return_value=gate) as api:
            res = self.client.route(
                "Explain how GLSL vertex shaders calculate lighting",
                [{"name": "threejs-shaders", "description": "Write GLSL shaders"}],
                tri_gate=True, rerank=False,
            )
        self.assertEqual(res.status, "no_skill_needed")
        self.assertAlmostEqual(res.specialist_noul or 0.0, 0.10, places=3)
        self.assertEqual(api.call_count, 1)
        questions = api.call_args.args[0]["questions"]
        self.assertIn("specialised_workflow", questions)
        self.assertNotIn("acts_on_user_system", questions)
        self.assertNotIn("prose_suffices", questions)
        self.assertIn("selected_skill", questions)
        criteria = questions["selected_skill"]["criteria"]
        self.assertIn("threejs-shaders", criteria)
        self.assertIn("__no_skill__", criteria)
        self.assertIn("__no_match__", criteria)

    def test_tri_gate_missing_answers_are_protocol_errors(self) -> None:
        with patch.object(self.client, "_call_api", return_value={"answers": {"selected_skill": {"choice": "threejs-shaders"}}}):
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
        self.noul = {"answers": {"specialised_workflow": {"noul": 0.92}}}
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

    def test_rerank_keeps_choice_winner_when_neighbor_fits_higher(self) -> None:
        rerank = {
            "answers": {
                "selected_skill": {
                    "choice": "pptx-author",
                    "confidence": 0.99,
                    "probabilities": {"pptx-author": 0.99, "powerpoint": 0.01},
                },
                "fits::powerpoint": {"noul": 0.73},
                "fits::pptx-author": {"noul": 0.38},
            }
        }
        with patch.object(self.client, "_call_api", side_effect=[self.noul, self.stage2, rerank]):
            res = self.client.route(
                "Author a new investor pitch deck from scratch",
                self.skills,
                rerank=True, tri_gate=False,
            )
        self.assertEqual(res.status, "routed")
        self.assertEqual(res.winner, "pptx-author")

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


if __name__ == "__main__":
    unittest.main()
