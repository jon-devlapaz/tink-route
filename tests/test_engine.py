import unittest
from pathlib import Path
from unittest.mock import MagicMock

from tink_route.core.engine import RoutingEngine
from tink_route.core.models import RoutingResult, PruneReport
from tink_route.adapters.executor import SubprocessExecutor
from tink_route.adapters.ledger import FilesystemLedger
from tink_route.adapters.client import JevRouterClient


class MockExecutor(SubprocessExecutor):
    def __init__(self, code: int = 0, stdout: str = "", stderr: str = ""):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[list[str], Path]] = []

    def run(self, cmd: list[str], cwd: Path) -> tuple[int, str, str]:
        self.calls.append((cmd, cwd))
        return self.code, self.stdout, self.stderr


class TestRoutingEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock(spec=JevRouterClient)
        self.executor = MockExecutor(code=0, stdout="success", stderr="")
        self.ledger = MagicMock(spec=FilesystemLedger)
        self.engine = RoutingEngine(
            client=self.client,
            executor=self.executor,
            ledger=self.ledger,
        )

    def test_engine_route_no_skill_needed(self) -> None:
        self.client.route.return_value = RoutingResult(
            status="no_skill_needed",
            task="simple task",
            specialist_noul=0.1,
            threshold=0.6,
            elapsed_ms=50,
        )
        res = self.engine.route("simple task", [{"name": "s1", "description": "desc"}])
        self.assertEqual(res.status, "no_skill_needed")
        self.assertFalse(res.installed)
        self.assertIsNone(res.activation)

    def test_engine_route_routed_without_install(self) -> None:
        self.client.route.return_value = RoutingResult(
            status="routed",
            task="render 3d",
            winner="threejs-shaders",
            probability=0.95,
            runner_up=None,
            runner_up_probability=0.0,
            margin=0.95,
            confidence=0.95,
            specialist_noul=0.9,
            threshold=0.6,
            elapsed_ms=120,
        )
        res = self.engine.route("render 3d", [{"name": "threejs-shaders", "description": "desc"}], install=False)
        self.assertEqual(res.status, "routed")
        self.assertEqual(res.winner, "threejs-shaders")
        self.assertFalse(res.installed)
        self.assertIsNotNone(res.activation)
        assert res.activation is not None
        self.assertEqual(res.activation["mode"], "install_required")

    def test_engine_route_routed_with_install_success(self) -> None:
        self.client.route.return_value = RoutingResult(
            status="routed",
            task="render 3d",
            winner="threejs-shaders",
            probability=0.95,
            confidence=0.95,
            specialist_noul=0.9,
            threshold=0.6,
            elapsed_ms=120,
        )
        self.ledger.lock.return_value.__enter__.return_value = None
        self.ledger.record_ephemeral_skill_locked.return_value = None

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            (project_path / ".agents" / "skills" / "threejs-shaders").mkdir(parents=True)
            res = self.engine.route(
                "render 3d",
                [{"name": "threejs-shaders", "description": "desc"}],
                install=True,
                ephemeral=True,
                project_dir=project_path,
            )
            self.assertEqual(res.status, "routed")
            self.assertTrue(res.installed)
            self.assertIsNotNone(res.activation)
            assert res.activation is not None
            self.assertEqual(res.activation["mode"], "direct_read")
            self.assertEqual(self.executor.calls[0][0], ["tink", "skill", "add", "--", "threejs-shaders"])

    def test_engine_prune_delegates_to_ledger(self) -> None:
        self.ledger.prune.return_value = PruneReport(
            pruned=["temp-skill"],
            preserved=["keep-skill"],
            errors=[],
            dry_run=False,
            count=1,
        )
        report = self.engine.prune(Path("/mock/project"), dry_run=False, all_unpinned=False)
        self.assertEqual(report.pruned, ["temp-skill"])
        self.assertEqual(report.count, 1)
        self.ledger.prune.assert_called_once_with(
            Path("/mock/project"), dry_run=False, all_unpinned=False, executor=self.executor
        )


if __name__ == "__main__":
    unittest.main()
