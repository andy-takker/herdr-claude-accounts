#!/usr/bin/env python3
"""Subprocess fixture: strict enough to catch incorrect CLI invocations."""
import json
import os
from pathlib import Path
import sys
import time

profile = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
fixture = json.loads((profile / 'fixture.json').read_text())
if fixture.get('pid_file'):
    Path(fixture['pid_file']).write_text(str(os.getpid()))
args = sys.argv[1:]
if fixture.get('sleep'):
    time.sleep(fixture['sleep'])
if args == ['auth', 'status', '--json']:
    if fixture.get('malformed'):
        print('not-json SECRET')
    else:
        print(json.dumps(fixture.get('auth', {
            'loggedIn': False, 'authMethod': 'none', 'configDirectory': str(profile)
        })))
    sys.exit(fixture.get('exit', 1))
elif args == ['agents', '--json']:
    print(json.dumps(fixture.get('sessions', [])))
    sys.exit(fixture.get('exit', 0))
elif args == ['auth', 'login', '--claudeai']:
    print('Browser login progress, not JSON')
    sys.exit(fixture.get('exit', 0))
elif '-p' in args:
    for flag in ('--safe-mode', '--strict-mcp-config', '--no-session-persistence', '--tools'):
        if flag not in args:
            sys.exit(88)
    if args[args.index('--tools') + 1] != '':
        sys.exit(89)
    if '--input-format' in args:
        requests = [json.loads(line) for line in sys.stdin if line.strip()]
        assert [r['request']['subtype'] for r in requests] == ['initialize', 'get_usage']
        assert all(r['type'] == 'control_request' for r in requests)
        print(json.dumps({'type':'control_response','response':{'subtype':'success',
                          'request_id':'usage','response':fixture.get('usage', {})}}))
        sys.exit(fixture.get('exit', 0))
    if 'stream-json' in args:
        for row in fixture['online'] if isinstance(fixture['online'], list) else [fixture['online']]:
            print(json.dumps(row))
    else:
        print(json.dumps(fixture['online']))
    sys.exit(fixture.get('exit', 0))
else:
    sys.exit(90)
