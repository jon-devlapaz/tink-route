"""Tests for protocol handling, retries, tournament reduction, sentinels, and candidate filtering."""

import io
import json
import unittest
import urllib.error
from typing import Any
from unittest.mock import MagicMock, patch

from tink_route.adapters.client import BATCH_SIZE, JevRouterClient
from tink_route.core.exceptions import ApiProtocolError, RoutingError


class TestProtocolAndReduction(unittest.TestCase):
    def setUp(self) -> None:
        self.client = JevRouterClient(api_key="test-key")

    def test_proto_1_network_timeout_and_json_errors_raise_api_protocol_error(self) -> None:
        """PROTO-1: URLError, TimeoutError, JSONDecodeError raise ApiProtocolError (inherits RuntimeError)."""
        # 1. URLError
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            with self.assertRaises(ApiProtocolError) as ctx:
                self.client._call_api({"test": 1})
            self.assertIsInstance(ctx.exception, RoutingError)
            self.assertIsInstance(ctx.exception, RuntimeError)
            self.assertIn("Connection refused", str(ctx.exception))

        # 2. TimeoutError
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Read timed out")):
            with self.assertRaises(ApiProtocolError) as ctx:
                self.client._call_api({"test": 1})
            self.assertIsInstance(ctx.exception, RuntimeError)
            self.assertIn("Read timed out", str(ctx.exception))

        # 3. JSONDecodeError
        bad_resp = MagicMock()
        bad_resp.read.return_value = b"Not JSON at all <html>"
        with patch("urllib.request.urlopen", return_value=bad_resp):
            bad_resp.__enter__.return_value = bad_resp
            with self.assertRaises(ApiProtocolError) as ctx:
                self.client._call_api({"test": 1})
            self.assertIsInstance(ctx.exception, RuntimeError)
            self.assertIn("Invalid JSON", str(ctx.exception))

    def test_proto_2_bounded_retry_backoff_429_and_5xx(self) -> None:
        """PROTO-2: Bounded retry for 429 with Retry-After and transient 502/503/504."""
        # 1. 429 with Retry-After header then succeeds
        mock_headers = MagicMock()
        mock_headers.get.return_value = "0.01"
        err_429 = urllib.error.HTTPError(
            url="https://api.typesafe.ai",
            code=429,
            msg="Too Many Requests",
            hdrs=mock_headers,
            fp=io.BytesIO(b"rate limit"),
        )
        good_resp = MagicMock()
        good_resp.read.return_value = b'{"success": true}'
        good_resp.__enter__.return_value = good_resp

        with patch("urllib.request.urlopen", side_effect=[err_429, good_resp]) as mock_open:
            with patch("time.sleep") as mock_sleep:
                result = self.client._call_api({"test": 1})
                self.assertEqual(result, {"success": True})
                self.assertEqual(mock_open.call_count, 2)
                mock_sleep.assert_called_once_with(0.01)

        # 2. 503 transient error then succeeds
        err_503 = urllib.error.HTTPError(
            url="https://api.typesafe.ai",
            code=503,
            msg="Service Unavailable",
            hdrs=mock_headers,
            fp=io.BytesIO(b"unavailable"),
        )
        with patch("urllib.request.urlopen", side_effect=[err_503, good_resp]) as mock_open:
            with patch("time.sleep") as mock_sleep:
                result = self.client._call_api({"test": 1})
                self.assertEqual(result, {"success": True})
                self.assertEqual(mock_open.call_count, 2)
                mock_sleep.assert_called_once()

    def test_proto_3_filter_raw_probabilities_to_candidate_set(self) -> None:
        """PROTO-3: In _parse_choice, only copy keys from raw_probabilities present in candidates."""
        response = {
            "answers": {
                "selected_skill": {
                    "choice": "skill-valid",
                    "confidence": 0.85,
                    "probabilities": {
                        "skill-valid": 0.85,
                        "hallucinated-skill": 0.99,
                        "another-random-string": 0.50,
                    },
                }
            }
        }
        candidates = {"skill-valid", "__no_skill__", "__no_match__"}
        parsed = self.client._parse_choice(response, candidates)

        self.assertEqual(parsed["winner"], "skill-valid")
        self.assertIn("skill-valid", parsed["probabilities"])
        self.assertNotIn("hallucinated-skill", parsed["probabilities"])
        self.assertNotIn("another-random-string", parsed["probabilities"])

    def test_proto_4_recursive_tournament_reduction(self) -> None:
        """PROTO-4: Recursive tournament reduction if len(survivors) > BATCH_SIZE."""
        # 55 skills with BATCH_SIZE=24 -> Round 1: 3 chunks (24, 24, 7)
        # All 3 chunks produce a winner -> 3 survivors.
        # Since 3 <= 24, Round 2 evaluates final batch of 3.
        # Now simulate 55 skills where BATCH_SIZE is patched to 3 to force 2+ rounds!
        # Test with 10 skills and BATCH_SIZE=2 to force 3 rounds of reduction:
        # Round 1: 5 chunks of 2 -> 5 winners (5 > 2)
        # Round 2: 3 chunks (2, 2, 1) -> 3 winners (3 > 2)
        # Round 3: 2 chunks (2, 1) -> 2 winners (2 <= 2)
        # Final batch: evaluates the 2 survivors.
        with patch("tink_route.adapters.client.BATCH_SIZE", 2):
            skills = [{"name": f"skill-{i}", "description": f"desc {i}"} for i in range(10)]
            noul_resp = {"answers": {"specialist_needed": {"noul": 0.95}}}

            call_count = [0]

            def fake_call_api(payload: dict[str, Any]) -> dict[str, Any]:
                call_count[0] += 1
                if "specialist_needed" in payload.get("questions", {}):
                    return noul_resp
                criteria = payload["questions"]["selected_skill"]["criteria"]
                skill_names = [k for k in criteria if not k.startswith("__")]
                self.assertLessEqual(len(skill_names), 2, "No batch may exceed BATCH_SIZE=2")
                # Choose the first skill name in candidates
                winner = skill_names[0]
                return {
                    "answers": {
                        "selected_skill": {
                            "choice": winner,
                            "confidence": 0.9,
                            "probabilities": {winner: 0.9},
                        }
                    }
                }

            with patch.object(self.client, "_call_api", side_effect=fake_call_api):
                res = self.client.route("Some task", skills, threshold=0.60)
                self.assertEqual(res["status"], "routed")
                self.assertIsNotNone(res["winner"])

    def test_proto_5_sentinel_precedence_highest_probability(self) -> None:
        """PROTO-5: Sentinel precedence selects highest probability sentinel across batches."""
        # 25 skills -> 2 batches
        skills = [{"name": f"skill-{i}", "description": f"desc {i}"} for i in range(25)]
        noul_resp = {"answers": {"specialist_needed": {"noul": 0.90}}}

        # Batch 1 returns __no_skill__ with p=0.98
        resp_b1 = {
            "answers": {
                "selected_skill": {
                    "choice": "__no_skill__",
                    "confidence": 0.98,
                    "probabilities": {"__no_skill__": 0.98},
                }
            }
        }
        # Batch 2 returns __no_match__ with p=0.30
        resp_b2 = {
            "answers": {
                "selected_skill": {
                    "choice": "__no_match__",
                    "confidence": 0.30,
                    "probabilities": {"__no_match__": 0.30},
                }
            }
        }

        with patch.object(self.client, "_call_api", side_effect=[noul_resp, resp_b1, resp_b2]):
            res = self.client.route("Simple task", skills, threshold=0.60)
            # __no_skill__ (0.98) has higher probability than __no_match__ (0.30)
            # Must NOT be overwritten with no_match!
            self.assertEqual(res["status"], "no_skill_needed")
            self.assertEqual(res["confidence"], 0.98)

    def test_proto_6_track_global_top_candidate_across_sentinel_batches(self) -> None:
        """PROTO-6: Track global top candidate and runner-up across all sentinel batches."""
        skills = [{"name": f"skill-{i}", "description": f"desc {i}"} for i in range(25)]
        noul_resp = {"answers": {"specialist_needed": {"noul": 0.90}}}

        # Batch 1 (skills 0..23): winner is __no_match__ (p=0.55), but skill-5 scored 0.45
        resp_b1 = {
            "answers": {
                "selected_skill": {
                    "choice": "__no_match__",
                    "confidence": 0.55,
                    "probabilities": {
                        "__no_match__": 0.55,
                        "skill-5": 0.45,
                    },
                }
            }
        }
        # Batch 2 (skill 24): winner is __no_match__ (p=0.51), but skill-24 scored 0.49
        resp_b2 = {
            "answers": {
                "selected_skill": {
                    "choice": "__no_match__",
                    "confidence": 0.51,
                    "probabilities": {
                        "__no_match__": 0.51,
                        "skill-24": 0.49,
                    },
                }
            }
        }

        with patch.object(self.client, "_call_api", side_effect=[noul_resp, resp_b1, resp_b2]):
            # Threshold 0.60 -> both winners fell below threshold, resulting in status "uncertain" or "no_match"
            res = self.client.route("Borderline task", skills, threshold=0.60)
            # Global top candidate across ALL batches should be skill-24 (0.49), runner-up skill-5 (0.45)
            self.assertEqual(res["top_candidate"], "skill-24")
            self.assertEqual(res["probability"], 0.49)
            self.assertEqual(res["runner_up"], "skill-5")
            self.assertEqual(res["runner_up_probability"], 0.45)
            self.assertEqual(res["margin"], 0.04)


if __name__ == "__main__":
    unittest.main()
