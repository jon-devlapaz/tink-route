"""Regression tests for publication boundary fixes; no live API required."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tink_route.client import JevRouterClient
from tink_route.cli import main
from tink_route.ephemeral import prune_ephemeral_skills
from tink_route.metadata import load_library_skills


class TestPublicationBoundaries(unittest.TestCase):
    def setUp(self):
        self.client = JevRouterClient('fixture')
        self.skills = [{'name': f's{i}', 'description': 'test'} for i in range(25)]
        self.need = {'answers': {'specialist_needed': {'noul': .9}}}

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
        for response in (None, {'answers': None}, {'answers': {'specialist_needed': {}}}):
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
            with patch.dict(os.environ, TYPESAFE_API_KEY='fixture'), patch.object(
                sys, 'argv', ['tink-route', '--json', '--library', str(root), 'test']
            ), patch('sys.stdout', output):
                self.assertEqual(main(), 2)
            self.assertIn('Duplicate skill name', json.loads(output.getvalue())['error'])

    def test_dry_run_does_not_create_lock_or_tink_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(prune_ephemeral_skills(root, dry_run=True).count, 0)
            self.assertEqual(list(root.iterdir()), [])

    def test_install_and_prune_cannot_interleave_before_recording(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from subprocess import CompletedProcess
        from tink_route.adapters.ledger import FilesystemLedger
        from tink_route.cli import _DEFAULT_ENGINE
        from tink_route.ephemeral import load_ephemeral_skills

        recording = threading.Event()
        release = threading.Event()
        pruning = threading.Event()
        removed = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / '.agents/skills/test/SKILL.md'

            def tink(args, **kwargs):
                if args[2] == 'add':
                    skill.parent.mkdir(parents=True)
                    skill.write_text('skill')
                else:
                    removed.set()
                    skill.unlink()
                return CompletedProcess(args, 0, '', '')

            original = FilesystemLedger.record_ephemeral_skill_locked

            def record(self, project, name):
                recording.set()
                if not release.wait(3):
                    raise TimeoutError('test did not release recording')
                return original(self, project, name)

            def prune():
                pruning.set()
                return prune_ephemeral_skills(root)

            with patch('subprocess.run', side_effect=tink), patch.object(
                FilesystemLedger, 'record_ephemeral_skill_locked', record
            ), ThreadPoolExecutor(max_workers=2) as pool:
                installer = pool.submit(_DEFAULT_ENGINE.install_and_track, 'test', root, True)
                try:
                    self.assertTrue(recording.wait(2))
                    pruner = pool.submit(prune)
                    self.assertTrue(pruning.wait(2))
                    self.assertFalse(removed.wait(.1))
                    self.assertFalse(pruner.done())
                finally:
                    release.set()
                self.assertTrue(installer.result(timeout=3).success)
                self.assertEqual(pruner.result(timeout=3).pruned, ['test'])
            self.assertFalse(skill.exists())
            self.assertEqual(load_ephemeral_skills(root), [])

    def test_unsupported_lock_backend_fails_before_install(self):
        from tink_route.cli import _DEFAULT_ENGINE
        with tempfile.TemporaryDirectory() as tmp, patch(
            'tink_route.adapters.ledger.fcntl', None
        ), patch('tink_route.adapters.ledger.msvcrt', None), patch('subprocess.run') as tink:
            with self.assertRaisesRegex(RuntimeError, 'No supported file-locking'):
                _DEFAULT_ENGINE.install_and_track('test', Path(tmp), True)
            tink.assert_not_called()
