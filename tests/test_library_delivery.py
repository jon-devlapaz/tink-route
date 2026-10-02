"""Library boundaries: refuse a different delivery source before routing or mounting.

Also preserve custom-library inspection and accept equivalent paths to Tink's library.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tink_route.cli import main
from tink_route.core.models import RoutingResult


class LibraryDeliveryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / 'home'
        self.library = self.home / 'skills'
        self.custom = self.root / 'custom'
        for library, body in ((self.library, 'DEFAULT'), (self.custom, 'CUSTOM')):
            (library / 'probe').mkdir(parents=True)
            (library / 'probe/SKILL.md').write_text(
                f'---\nname: probe\ndescription: {body}\n---\n{body}\n')
        env = patch.dict(os.environ, {'TINK_HOME': str(self.home), 'TINK_ROUTE_RECEIPT': ''})
        env.start()
        self.addCleanup(env.stop)

    def invoke(self, *argv, route_fn):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(['--json', *argv, 'fixture task'], route_fn=route_fn)
        return code, json.loads(out.getvalue())

    def test_custom_delivery_refused_before_routing_or_mount(self):
        with patch('tink_route.cli.DefaultSubprocessExecutor') as executor:
            for extra in ([], ['--inline-max', '0']):
                with self.subTest(extra=extra):
                    code, result = self.invoke('--library', str(self.custom), *extra,
                        route_fn=lambda *args: self.fail('must not call the routing service'))
                    self.assertEqual(code, 2)
                    self.assertEqual(result['reason'], 'library_mismatch')
                    self.assertIsNone(result['content'])
            executor.return_value.run.assert_not_called()

    def test_custom_pick_remains_available(self):
        def route(task, skills, args):
            self.assertEqual(skills[0]['body'], 'CUSTOM')
            return RoutingResult(status='routed', task=task, winner='probe', confidence=1.0)
        code, result = self.invoke('--pick', '--library', str(self.custom), route_fn=route)
        self.assertEqual(code, 0)
        self.assertEqual(result['winner'], 'probe')

    def test_equivalent_library_path_is_allowed(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.library, target_is_directory=True)
        seen = []
        def route(task, skills, args):
            seen.append(skills[0]['body'])
            return RoutingResult(status='no_candidates_available', task=task)
        code, result = self.invoke('--library', str(alias), route_fn=route)
        self.assertEqual(code, 1)
        self.assertEqual(seen, ['DEFAULT'])
        self.assertEqual(result['status'], 'no_skill')
