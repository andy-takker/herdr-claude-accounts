"""Exercise an installed wheel from outside the checkout, using only temporary data."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    wheel = parser.parse_args().wheel.resolve()
    with tempfile.TemporaryDirectory(prefix='claude-accounts-wheel-') as tmp:
        root = Path(tmp)
        environment = root / 'venv'
        home = root / 'home'
        home.mkdir()
        venv.EnvBuilder(with_pip=True).create(environment)
        python = str(environment / 'bin/python')
        env = dict(os.environ, HOME=str(home))
        env.pop('PYTHONPATH', None)
        env.pop('CLAUDE_CONFIG_DIR', None)
        for key in tuple(env):
            if key.startswith(('ANTHROPIC_', 'CLAUDE_CODE_OAUTH_', 'CLAUDE_CODE_USE_')):
                env.pop(key)

        def run(*args):
            return subprocess.run(args, cwd=root, env=env, check=True,
                                  capture_output=True, text=True, timeout=60).stdout

        run(python, '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel))
        manager = str(environment / 'bin/claude-accounts')
        account = json.loads(run(manager, 'add', 'Wheel test', '--no-login'))
        state = json.loads(run(python, '-m', 'herdr_claude_accounts', 'list'))
        assert state['accounts'][0]['id'] == account['id']
        run(manager, 'install-shell', '--binary', '/bin/echo')
        run(manager, 'select', account['id'])
        assert run(str(environment / 'bin/claude-accounts-launch'), '--resume', 'example').strip() == '--resume example'
        launchers = home / '.local/share/herdr-claude-accounts/bin'
        assert run(str(launchers / 'claude'), 'wheel-launch').strip() == 'wheel-launch'
        assert json.loads(run(str(launchers / 'claude-accounts'), 'list'))['selected'] == account['id']
        print('Wheel installed outside checkout: module, console commands and shell launchers passed.')


if __name__ == '__main__':
    main()
