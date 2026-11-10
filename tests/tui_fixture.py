#!/usr/bin/env python3
"""Stateful CLI double for the isolated terminal account workflow."""
import json
import os
from pathlib import Path
import sys
import time

root = Path(os.environ['TUI_FIXTURE_ROOT'])
profile = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
profile.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
if args == ['auth', 'login', '--claudeai']:
    (root / 'login.pid').write_text(str(os.getpid()))
    while not (root / 'finish-login').exists():
        time.sleep(.05)
    (profile / 'signed-in').write_text('fixture only')
elif args == ['auth', 'status', '--json']:
    saved = profile.name == '.claude' or (profile / 'signed-in').exists()
    print(json.dumps({'loggedIn': saved, 'authMethod': 'claude.ai' if saved else 'none',
                      'email': profile.name + '@example.test', 'orgId': 'fixture'}))
    sys.exit(0 if saved else 1)
elif args == ['agents', '--json']:
    print(json.dumps([{'id': 'fixture-session', 'state': 'idle', 'cwd': '/fixture-session-path'}]))
elif '--input-format' in args:
    requests = [json.loads(line) for line in sys.stdin if line.strip()]
    assert [r['request']['subtype'] for r in requests] == ['initialize', 'get_usage']
    with (root / 'usage-calls').open('a') as log:
        log.write(profile.name + '\n')
    print(json.dumps({'type': 'control_response', 'response': {'subtype': 'success',
                     'request_id': 'usage', 'response': {'rate_limits_available': True,
                     'rate_limits': {'limits': [{'group': 'weekly', 'percent': 80,
                     'resets_at': '2099-11-11T12:00:00Z'}]}}}}))
else:
    sys.exit(90)
