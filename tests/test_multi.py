"""Tests for compound multi-skill routing (--multi)."""

import unittest
from unittest.mock import patch

from tink_route.adapters.client import JevRouterClient


def _gate(high: float = 0.9) -> dict:
    return {"answers": {"specialist_needed": {"noul": high}}}


def _stage2(choice: str, probs: dict) -> dict:
    return {
        "answers": {
            "selected_skill": {
                "choice": choice,
                "confidence": probs[choice],
                "probabilities": dict(probs),
            }
        }
    }


SKILLS = [
    {"name": "skill-a", "description": "Do A"},
    {"name": "skill-b", "description": "Do B"},
    {"name": "skill-c", "description": "Do C"},
]


class TestMultiRouting(unittest.TestCase):
    def setUp(self) -> None:
        self.client = JevRouterClient(api_key="test-key")

    def test_multi_returns_ranked_list_capped_by_top_k(self) -> None:
        probs = {"skill-a": 0.90, "skill-b": 0.75, "skill-c": 0.50}
        with patch.object(
            self.client, "_call_api", side_effect=[_gate(), _stage2("skill-a", probs)]
        ) as api:
            res = self.client.route("Do A then B", SKILLS, multi=True, top_k=2, tri_gate=False, rerank=False)
        self.assertEqual(res.status, "multi_routed")
        self.assertEqual(res.winner, "skill-a")
        self.assertEqual(
            res.candidates,
            [
                {"skill": "skill-a", "probability": 0.90},
                {"skill": "skill-b", "probability": 0.75},
            ],
        )
        self.assertEqual(api.call_count, 2)

    def test_multi_filters_below_threshold(self) -> None:
        probs = {"skill-a": 0.90, "skill-b": 0.40, "skill-c": 0.30}
        with patch.object(
            self.client, "_call_api", side_effect=[_gate(), _stage2("skill-a", probs)]
        ):
            res = self.client.route("Do A", SKILLS, threshold=0.6, multi=True, top_k=3, tri_gate=False, rerank=False)
        self.assertEqual(res.status, "multi_routed")
        self.assertEqual(len(res.candidates or []), 1)

    def test_multi_falls_back_when_none_qualify(self) -> None:
        probs = {"skill-a": 0.50, "skill-b": 0.40, "skill-c": 0.30}
        with patch.object(
            self.client, "_call_api", side_effect=[_gate(), _stage2("skill-a", probs)]
        ):
            res = self.client.route("Do A", SKILLS, threshold=0.6, multi=True, top_k=3, tri_gate=False, rerank=False)
        self.assertEqual(res.status, "uncertain")
        self.assertIsNone(res.candidates)

    def test_top_k_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.client.route("Do A", SKILLS, multi=True, top_k=0, tri_gate=False, rerank=False)
        with self.assertRaises(ValueError):
            self.client.route("Do A", SKILLS, multi=True, top_k=11, tri_gate=False, rerank=False)

    def test_multi_rerank_fits_veto_is_no_match(self) -> None:
        probs = {"skill-a": 0.70, "skill-b": 0.65, "skill-c": 0.20}
        rerank = {
            "answers": {
                "selected_skill": {
                    "choice": "skill-a",
                    "confidence": 0.80,
                    "probabilities": {"skill-a": 0.80, "skill-b": 0.40},
                },
                "fits::skill-a": {"noul": 0.10},
                "fits::skill-b": {"noul": 0.12},
                "fits::skill-c": {"noul": 0.05},
            }
        }
        rich = [
            {**s, "description_full": s["description"], "body": f"Body for {s['name']}"}
            for s in SKILLS
        ]
        with patch.object(
            self.client, "_call_api", side_effect=[_gate(), _stage2("skill-a", probs), rerank]
        ):
            res = self.client.route("Do something else", rich, multi=True, top_k=2, rerank=True, tri_gate=False)
        self.assertEqual(res.status, "no_match")
        self.assertIsNone(res.candidates)
        self.assertIsNone(res.winner)


if __name__ == "__main__":
    unittest.main()
