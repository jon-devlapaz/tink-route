"""Tests for input validation, path containment, YAML chomping, BOM stripping, and CLI arg safety."""

import tempfile
import unittest
from pathlib import Path

from tink_route.core.exceptions import SkillValidationError
from tink_route.core.validation import is_valid_skill_name, validate_skill_dir_containment
from tink_route.metadata import parse_skill_metadata


class TestSecurityValidationAndMetadata(unittest.TestCase):
    def test_is_valid_skill_name(self) -> None:
        """Validate skill names against ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$."""
        # Valid names
        self.assertTrue(is_valid_skill_name("valid-skill"))
        self.assertTrue(is_valid_skill_name("skill_123"))
        self.assertTrue(is_valid_skill_name("a"))
        self.assertTrue(is_valid_skill_name("A1-b_C"))
        self.assertTrue(is_valid_skill_name("x" * 64))

        # Invalid names
        self.assertFalse(is_valid_skill_name("-leading-dash"))
        self.assertFalse(is_valid_skill_name("_leading-underscore"))
        self.assertFalse(is_valid_skill_name("has/slash"))
        self.assertFalse(is_valid_skill_name("../traversal"))
        self.assertFalse(is_valid_skill_name("has space"))
        self.assertFalse(is_valid_skill_name("has\nnewline"))
        self.assertFalse(is_valid_skill_name(""))
        self.assertFalse(is_valid_skill_name("x" * 65))
        self.assertFalse(is_valid_skill_name("skïll"))  # non-ascii
        self.assertFalse(is_valid_skill_name(None))  # type: ignore[arg-type]

    def test_validate_skill_dir_containment(self) -> None:
        """Ensure skill directory is strictly contained within project .agents/skills."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            base_dir = (project_dir / ".agents" / "skills").resolve()

            # Valid name
            path = validate_skill_dir_containment(project_dir, "my-skill")
            self.assertEqual(path, base_dir / "my-skill")
            self.assertTrue(path.is_relative_to(base_dir))

            # Traversal attempt
            for malicious in ("../escape", "../../etc", "foo/bar", "-bad-flag", ""):
                with self.subTest(name=malicious):
                    with self.assertRaises(SkillValidationError):
                        validate_skill_dir_containment(project_dir, malicious)

    def test_meta_1_utf8_bom_stripping(self) -> None:
        """META-1: parse_skill_metadata strips leading UTF-8 BOM."""
        content_with_bom = "\ufeff---\nname: bom-skill\ndescription: Handled with BOM\n---\n# Body"
        meta = parse_skill_metadata(content_with_bom, fallback_name="fallback")
        self.assertEqual(meta["name"], "bom-skill")
        self.assertEqual(meta["description"], "Handled with BOM")

    def test_meta_2_yaml_chomping_and_paragraphs(self) -> None:
        """META-2: Folded and literal YAML blocks preserve paragraphs and strip the trailing break."""
        # Folded chomped (>-) with two paragraphs separated by blank line
        yaml_folded = """---
name: folded-skill
description: >-
  Paragraph one line one
  and line two.

  Paragraph two line one
  and line two.
---
"""
        meta = parse_skill_metadata(yaml_folded, fallback_name="fallback")
        expected = "Paragraph one line one and line two.\n\nParagraph two line one and line two."
        self.assertEqual(meta["description"], expected)

        # Literal chomped (|-)
        yaml_literal = """---
name: literal-skill
description: |-
  Line one
  Line two
---
"""
        meta_lit = parse_skill_metadata(yaml_literal, fallback_name="fallback")
        self.assertEqual(meta_lit["description"], "Line one\nLine two")

    def test_meta_3_validate_skill_names_in_metadata(self) -> None:
        """META-3: Validate skill names in metadata against is_valid_skill_name."""
        invalid_yaml = """---
name: "../malicious"
description: An invalid skill.
---
"""
        with self.assertRaises(SkillValidationError):
            parse_skill_metadata(invalid_yaml, fallback_name="fallback")

        valid_yaml = """---
description: An unnamed skill.
---
"""
        with self.assertRaises(SkillValidationError):
            parse_skill_metadata(valid_yaml, fallback_name="-invalid-fallback")


if __name__ == "__main__":
    unittest.main()
