"""Issue 08 (parts not waiting on Q2): registry publish entries, publisher-mirroring validation, size report."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import registry_entries as reg  # noqa: E402

SLUGS = ("asynx-image-pro", "asynx-audio", "asynx-video", "asynx-3d", "asynx-workbench")


class RegistryEntriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.out = Path(tempfile.mkdtemp())
        cls.report = reg.build_registry_entries(cls.out)
        cls.entries = {slug: json.loads((cls.out / f"{slug}.json").read_text(encoding="utf-8")) for slug in SLUGS}

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_one_entry_per_package_with_publisher_metadata(self) -> None:
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted([f"{s}.json" for s in SLUGS] + ["size-report.md"]))
        for slug, entry in self.entries.items():
            m = entry["metadata"]
            self.assertEqual(set(entry), {"metadata", "files"})
            self.assertEqual((m["id"], m["name"], m["kind"], m["visibility"]), (f"plaiers/{slug}", f"plaiers-{slug}", "capability", "public"))
            self.assertIs(m["requirements"]["paidServices"], True)
            self.assertEqual(m["verification"]["status"], "dev-preview")
            self.assertTrue(m["verification"]["scope"] and m["description"] and m["keywords"])
            self.assertIn(f"name: plaiers-{slug}\n", entry["files"]["SKILL.md"])
            self.assertTrue(all(isinstance(v, str) for v in entry["files"].values()))
            self.assertFalse(any(p.startswith(".") or "/." in p or "__pycache__" in p for p in entry["files"]), slug)
            self.assertLessEqual(len(entry["files"]), 200)
            reg.validate_entry(entry)

    def test_core_entries_exclude_workbench_files(self) -> None:
        for slug in SLUGS[:4]:
            self.assertFalse(any("/ui/" in p or p.endswith("ui_server.py") or "/local_" in p for p in self.entries[slug]["files"]), slug)
        self.assertIn("scripts/asxlib/ui_server.py", self.entries["asynx-workbench"]["files"])

    def test_predicted_version_is_deterministic_and_origin_normalized(self) -> None:
        entry = self.entries["asynx-image-pro"]
        version = reg.predicted_version(entry)
        self.assertRegex(version, r"^[a-f0-9]{64}$")
        self.assertEqual(version, reg.predicted_version(copy.deepcopy(entry)))
        crlf = copy.deepcopy(entry)
        crlf["files"]["SKILL.md"] = crlf["files"]["SKILL.md"].replace("\n", "\r\n")
        self.assertEqual(reg.predicted_version(crlf), version)
        self.assertEqual({r["dev_version"] for r in self.report if r["id"] == "plaiers/asynx-image-pro"}, {version})

    def test_size_report(self) -> None:
        text = (self.out / "size-report.md").read_text(encoding="utf-8")
        for slug in SLUGS:
            self.assertIn(f"`plaiers/{slug}`", text)
        self.assertIn("Shared runtime (pillow==", text)
        self.assertIn(".whl", text)
        self.assertIn("Nothing was published", text)

    def assert_rejected(self, mutate, pattern: str) -> None:
        entry = copy.deepcopy(self.entries["asynx-audio"])
        mutate(entry)
        with self.assertRaisesRegex(reg.RegistryEntryError, pattern):
            reg.validate_entry(entry)

    def test_validation_mirrors_publisher_and_scans_for_credentials(self) -> None:
        self.assert_rejected(lambda e: e["metadata"].update(id="Plaiers/Bad"), "Invalid namespaced skill ID/name")
        self.assert_rejected(lambda e: e["metadata"].update(name="asynx-audio"), "Invalid namespaced skill ID/name")
        self.assert_rejected(lambda e: e["metadata"].update(kind="technique"), "kind must be capability")
        self.assert_rejected(lambda e: e["metadata"].pop("license"), "Invalid metadata license")
        self.assert_rejected(lambda e: e["metadata"]["requirements"].pop("paidServices"), "explicitly reviewed public metadata")
        self.assert_rejected(lambda e: e["metadata"].update(keywords="audio"), "Invalid metadata keywords")
        self.assert_rejected(lambda e: e["files"].update({"font.ttf": b"\x00\x01"}), "Non-text file: font.ttf")
        self.assert_rejected(lambda e: e["files"].update({".asx-install.json": "{}"}), "Unsupported package file: .asx-install.json")
        self.assert_rejected(lambda e: e["files"].update({f"f{i}.md": "x" for i in range(200)}), "Invalid skill files")
        self.assert_rejected(lambda e: e["files"].update({"big.md": "x" * (4 * 1024 * 1024)}), "Package too large")
        self.assert_rejected(lambda e: e["files"].update({"SKILL.md": "---\nname: asynx-audio\n---\n"}), "Wrong skill entrypoint")
        for secret, label in (("sagein_pat_abcdefghijkl", "sagein_pat_"),
                              ("eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ1c2VyLTEifQ.c2lnbmF0dXJlLXZhbHVl", "JWT"),
                              ("asx-0123456789abcdef0123456789ab", "asx- key")):
            self.assert_rejected(lambda e, s=secret: e["files"].update({"references/leak.md": f"token {s}\n"}),
                                 f"Credential-looking string \\({label}\\)")


if __name__ == "__main__":
    unittest.main()
