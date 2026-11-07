from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from dops.manifest import write_manifest, read_manifest, manifest_summary, InvalidManifestError

class ManifestPreservationTests(unittest.TestCase):
    def test_rebinding_preserves_comments_custom_tables_and_mcp_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / ".decisionops/manifest.toml"
            p.parent.mkdir()
            p.write_text('# team binding\nversion = 1\norg_id = "org"\nproject_id = "org:default" # selected project\nrepo_ref = "acme/repo"\nrepo_id = "repo-hint"\ndefault_branch = "release"\nmcp_server_name = "team-server"\nmcp_server_url = "https://api.aidecisionops.com/mcp"\n[team]\nowner = "Payments"\n')
            write_manifest(folder,{"org_id":"org","project_id":"real-project-id","repo_ref":"acme/repo"})
            after = p.read_text()
            self.assertIn('# team binding', after)
            self.assertIn('# selected project', after)
            parsed = read_manifest(folder)
            self.assertEqual(parsed['project_id'],'real-project-id')
            self.assertEqual(parsed['default_branch'],'release')
            self.assertEqual(parsed['mcp_server_name'],'team-server')
            self.assertEqual(parsed['repo_id'],'repo-hint')
            self.assertEqual(parsed['team']['owner'],'Payments')
            write_manifest(folder,{"org_id":"org","project_id":"other","repo_ref":"acme/other","repo_id":None})
            self.assertNotIn('repo_id',read_manifest(folder))

    def test_invalid_and_future_manifests_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'.decisionops/manifest.toml';p.parent.mkdir()
            for text in ['org_id = [','version = 2\norg_id = "org"\n']:
                p.write_text(text)
                with self.assertRaises(InvalidManifestError):
                    write_manifest(folder,{"org_id":"org","project_id":"project","repo_ref":"acme/repo"})
                self.assertEqual(p.read_text(),text)

    def test_summary_emits_only_binding_and_rejects_bad_input(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'.decisionops/manifest.toml';p.parent.mkdir()
            p.write_text('version=1\norg_id="org"\nproject_id="project"\nrepo_ref="acme/repo"\ncustom_secret="private"\n')
            self.assertNotIn('custom_secret',manifest_summary(folder))
            for content in ['version=1\norg_id="org"\n', 'version=1\norg_id=42\nproject_id="p"\nrepo_ref="o/r"\n']:
                p.write_text(content)
                with self.assertRaises(InvalidManifestError):manifest_summary(folder)
            p.write_bytes(b'\xff\xfe')
            with self.assertRaises(InvalidManifestError):manifest_summary(folder)
