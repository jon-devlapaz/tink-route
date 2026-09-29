"""Regression tests for publication boundary fixes; no live API required."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tink_route.adapters.client import JevRouterClient
from tink_route.cli import main
from tink_route.metadata import load_library_skills


class TestPublicationBoundaries(unittest.TestCase):
    def setUp(self):
        self.client = JevRouterClient('fixture')
        self.skills = [{'name': f's{i}', 'description': 'test'} for i in range(25)]
        self.need = {'answers': {'specialised_workflow': {'noul': .9}}}

    @staticmethod
    def choice(name, probability=.9):
        return {'answers': {'selected_skill': {
            'choice': name, 'confidence': .9, 'probabilities': {name: probability}}}}

    def test_invalid_batch_choice_cannot_escape_through_zero_survivors(self):
        for name in ('../outside', 's24'):
            with self.subTest(name=name), patch.object(
                self.client, '_call_api', side_effect=[self.need, self.choice(name)]
            ):
                with self.assertRaisesRegex(RuntimeError, 'invalid candidate'):
                    self.client.route('test', self.skills, tri_gate=False, rerank=False)

    def test_final_reduction_rejects_eliminated_candidate(self):
        responses = [self.need, self.choice('s0'), self.choice('s24'), self.choice('s1')]
        with patch.object(self.client, '_call_api', side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, 'invalid candidate'):
                self.client.route('test', self.skills, tri_gate=False, rerank=False)

    def test_nonfinite_and_out_of_range_scores_rejected(self):
        for value in (float('nan'), float('inf'), -0.1, 1.1):
            with self.subTest(value=value), patch.object(
                self.client, '_call_api', side_effect=[self.need, self.choice('s0', value)]
            ):
                with self.assertRaises(RuntimeError):
                    self.client.route('test', self.skills[:1], tri_gate=False, rerank=False)

    def test_malformed_answers_fail_as_operational_errors(self):
        for response in (None, {'answers': None}, {'answers': {'specialised_workflow': {}}}):
            with self.subTest(response=response), patch.object(self.client, '_call_api', return_value=response):
                with self.assertRaises(RuntimeError):
                    self.client.route('test', self.skills, tri_gate=False, rerank=False)
        for response in ({}, {'answers': None}, {'answers': {'selected_skill': {'choice': []}}}):
            with self.subTest(response=response), patch.object(self.client, '_call_api', side_effect=[self.need, response]):
                with self.assertRaises(RuntimeError):
                    self.client.route('test', self.skills, tri_gate=False, rerank=False)

    def test_invalid_threshold_rejected_before_api_call(self):
        with patch.object(self.client, '_call_api') as api:
            for threshold in (float('nan'), float('inf'), -1, 2):
                with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                    self.client.route('test', self.skills, threshold, tri_gate=False, rerank=False)
            api.assert_not_called()

    def test_duplicate_library_names_fail_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a', 'b'):
                (root / name).mkdir()
                (root / name / 'SKILL.md').write_text('---\nname: duplicate\ndescription: test\n---\n')
            with self.assertRaisesRegex(ValueError, 'Duplicate skill name'):
                load_library_skills(root)
            output = io.StringIO()
            with patch.dict(os.environ, TYPESAFE_API_KEY='fixture'), patch('sys.stdout', output):
                self.assertEqual(main(['--json', '--library', str(root), 'test']), 2)
            self.assertEqual(json.loads(output.getvalue())['reason'], 'library_unreadable')
