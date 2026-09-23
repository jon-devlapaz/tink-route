"""Tests for compound multi-skill routing (--multi)."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tink_route.adapters.client import JevRouterClient
from tink_route.core.engine import RoutingEngine


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
            res = self.client.route("Do A then B", SKILLS, multi=True, top_k=2)
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
            res = self.client.route("Do A", SKILLS, threshold=0.6, multi=True, top_k=3)
        self.assertEqual(res.status, "multi_routed")
        self.assertEqual(len(res.candidates or []), 1)

    def test_multi_falls_back_when_none_qualify(self) -> None:
        probs = {"skill-a": 0.50, "skill-b": 0.40, "skill-c": 0.30}
        with patch.object(
            self.client, "_call_api", side_effect=[_gate(), _stage2("skill-a", probs)]
        ):
            res = self.client.route("Do A", SKILLS, threshold=0.6, multi=True, top_k=3)
        self.assertEqual(res.status, "uncertain")
        self.assertIsNone(res.candidates)

    def test_multi_off_preserves_single_winner(self) -> None:
        probs = {"skill-a": 0.90, "skill-b": 0.75, "skill-c": 0.50}
        with patch.object(
            self.client, "_call_api", side_effect=[_gate(), _stage2("skill-a", probs)]
        ):
            res = self.client.route("Do A", SKILLS, multi=False)
        self.assertEqual(res.status, "routed")
        self.assertEqual(res.winner, "skill-a")
        self.assertIsNone(res.candidates)

    def test_top_k_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.client.route("Do A", SKILLS, multi=True, top_k=0)
        with self.assertRaises(ValueError):
            self.client.route("Do A", SKILLS, multi=True, top_k=11)

    def test_multi_with_rerank_keeps_fits_and_candidates(self) -> None:
        probs = {"skill-a": 0.70, "skill-b": 0.65, "skill-c": 0.20}
        rerank = {
            "answers": {
                "selected_skill": {
                    "choice": "skill-b",
                    "confidence": 0.88,
                    "probabilities": {"skill-b": 0.88, "skill-a": 0.60},
                },
                "fits::skill-b": {"noul": 0.91},
                "fits::skill-a": {"noul": 0.80},
                "fits::skill-c": {"noul": 0.10},
            }
        }
        rich = [
            {**s, "description_full": s["description"], "body": f"Body for {s['name']}"}
            for s in SKILLS
        ]
        with patch.object(
            self.client, "_call_api", side_effect=[_gate(), _stage2("skill-a", probs), rerank]
        ):
            res = self.client.route("Do B primarily", rich, multi=True, top_k=2, rerank=True)
        self.assertEqual(res.status, "multi_routed")
        self.assertEqual(res.winner, "skill-b")
        self.assertIsNotNone(res.fits)
        self.assertEqual((res.candidates or [])[0]["skill"], "skill-b")


class TestMultiEngine(unittest.TestCase):
    def test_engine_passes_multi_and_installs_winner(self) -> None:
        from tink_route.adapters.executor import SubprocessExecutor
        from tink_route.adapters.ledger import FilesystemLedger

        class OkExecutor(SubprocessExecutor):
            def run(self, cmd: list, cwd: Path) -> tuple[int, str, str]:
                return 0, "ok", ""

        client = MagicMock()
        client.route.return_value = {
            "status": "multi_routed",
            "task": "Do A and B",
            "winner": "skill-a",
            "probability": 0.9,
            "threshold": 0.6,
            "elapsed_ms": 10,
            "candidates": [
                {"skill": "skill-a", "probability": 0.9},
                {"skill": "skill-b", "probability": 0.75},
            ],
        }
        ledger = MagicMock(spec=FilesystemLedger)
        ledger.lock.return_value.__enter__.return_value = None
        engine = RoutingEngine(client=client, executor=OkExecutor(), ledger=ledger)
        res = engine.route("Do A and B", SKILLS, install=False, multi=True, top_k=2)
        self.assertEqual(res.status, "multi_routed")
        self.assertEqual(res.winner, "skill-a")
        client.route.assert_called_once()
        _, kwargs = client.route.call_args
        self.assertTrue(kwargs.get("multi"))
        self.assertEqual(kwargs.get("top_k"), 2)


if __name__ == "__main__":
    unittest.main()
