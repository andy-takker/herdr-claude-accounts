import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_module_and_absolute_entrypoint_share_the_same_private_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'store'
            env = dict(os.environ, HOME=tmp)
            module = subprocess.run(
                [sys.executable, '-m', 'herdr_claude_accounts', '--root', str(root), 'add', 'Work', '--no-login'],
                cwd=REPO, env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(module.returncode, 0, module.stderr)
            account = json.loads(module.stdout)
            direct = subprocess.run(
                [sys.executable, str(REPO / 'herdr_claude_accounts/__main__.py'), '--root', str(root), 'list'],
                cwd=tmp, env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(direct.returncode, 0, direct.stderr)
            self.assertEqual(json.loads(direct.stdout)['accounts'][0]['id'], account['id'])


if __name__ == '__main__':
    unittest.main()
