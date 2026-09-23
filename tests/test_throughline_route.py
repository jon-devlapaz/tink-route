import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tink_route.client import JevRouterClient
from tink_route.metadata import load_library_skills

HOME_SKILLS = Path.home() / ".tink" / "skills"
SKILL_NAMES = ("interrogate", "skill-scout", "ai-native-sdlc")


def skill_source(name: str) -> Path:
    path = HOME_SKILLS / name / "SKILL.md"
    if path.is_file():
        return path
    raise FileNotFoundError(f"No SKILL.md for {name} in {HOME_SKILLS}")


def stage_that_failed(expected: str, result: dict) -> str:
    noul = result.get("specialist_noul")
    threshold = result.get("threshold", 0.60)
    status = result.get("status")
    if expected == "no_skill_needed":
        if noul is not None and noul >= threshold:
            return "stage 1"
        return "stage 2"
    if status == "no_skill_needed" and noul is not None and noul < threshold:
        return "stage 1"
    return "stage 2"


class TestThroughlineRouteEval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._sandbox = None
        api_key = os.environ.get("TYPESAFE_API_KEY", "")
        if not api_key:
            raise unittest.SkipTest("TYPESAFE_API_KEY is not set")
        cls._sandbox = tempfile.TemporaryDirectory()
        library = Path(cls._sandbox.name)
        for name in SKILL_NAMES:
            destination = library / name
            destination.mkdir()
            shutil.copyfile(skill_source(name), destination / "SKILL.md")
        cls.skills = load_library_skills(library)
        cls.client = JevRouterClient(api_key=api_key)

    @classmethod
    def tearDownClass(cls):
        sandbox = getattr(cls, "_sandbox", None)
        if sandbox is not None:
            sandbox.cleanup()

    def setUp(self):
        loaded = {skill["name"] for skill in self.skills}
        self.assertEqual(loaded, set(SKILL_NAMES))

    def _expect(self, expected: str, task: str):
        result = self.client.route(task=task, skills=self.skills)
        actual = result["winner"] if result.get("status") == "routed" else result.get("status")
        self.assertEqual(
            actual,
            expected,
            (
                f"expected {expected!r}, got {actual!r}; "
                f"failed at {stage_that_failed(expected, result)}; "
                f"status={result.get('status')} "
                f"specialist_noul={result.get('specialist_noul')} "
                f"winner={result.get('winner')} "
                f"probability={result.get('probability')} "
                f"top_candidate={result.get('top_candidate')}"
            ),
        )

    def test_interrogate_plan_before_coding(self):
        self._expect(
            "interrogate",
            "Before any coding, grill this plan and pressure-test it. "
            "Interrogate the assumptions, find the holes, and identify the missing decisions.",
        )

    def test_skill_scout_before_writing_a_skill(self):
        self._expect(
            "skill-scout",
            "Find and qualify an existing agent skill for this workflow before writing a new one. "
            "Scout the library and compare candidates against the evidence we already have.",
        )

    def test_ai_native_sdlc_run(self):
        self._expect(
            "ai-native-sdlc",
            "Start an evidence-based SDLC run for this change, with stage contracts and test receipts. "
            "Set up the workflow and create the run.",
        )

    def test_rename_local_variable_needs_no_skill(self):
        self._expect(
            "no_skill_needed",
            "Rename the local variable tmp to elapsed_ms in this function.",
        )

    def test_add_docstring_needs_no_skill(self):
        self._expect(
            "no_skill_needed",
            "Add a docstring to the parse_skill_metadata function.",
        )

    def test_fix_comment_typo_needs_no_skill(self):
        self._expect(
            "no_skill_needed",
            "Fix a typo in the comment above the retry loop.",
        )


if __name__ == "__main__":
    unittest.main()
