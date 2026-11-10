"""Let Claude own credentials, token refresh and session discovery."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from herdr_claude_accounts.accounts import AccountError


AUTH_ENV = (
    'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN',
    'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR', 'CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR',
    'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
    'ANTHROPIC_BASE_URL', 'ANTHROPIC_PROFILE', 'CLAUDE_CODE_SIMPLE',
)


@contextmanager
def termination_signals():
    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    handlers = {sig: signal.signal(sig, terminate) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def managed_environment(profile, env=None):
    result = system_environment(env)
    conflicts = [key for key in AUTH_ENV if result.get(key)]
    if conflicts:
        raise AccountError('Conflicting authentication environment: ' + ', '.join(conflicts) +
                           '. Unset it or use System default.')
    result['CLAUDE_CONFIG_DIR'] = str(Path(profile).absolute())
    return result


def system_environment(env=None):
    result = dict(os.environ if env is None else env)
    result.pop('CLAUDE_CONFIG_DIR', None)
    # These describe Orca's own routing, not this independent profile.
    for key in ('ORCA_CLAUDE_PROFILE_POINTER', 'ORCA_CLAUDE_INJECTED_CONFIG_DIR',
                'ORCA_CLAUDE_USER_CONFIG_DIR'):
        result.pop(key, None)
    return result


class Claude:
    def __init__(self, binary, env=None, timeout=15, cancel=None):
        self.binary = str(binary)
        self.env = dict(os.environ if env is None else env)
        self.timeout = timeout
        self.cancel = cancel

    def _json(self, profile, args, timeout=None, input_text=None):
        env = managed_environment(profile, self.env) if profile is not None else system_environment(self.env)
        self.failure = 'command_failed'
        if self.cancel is not None and self.cancel.is_set():
            return None, None
        with tempfile.TemporaryDirectory(prefix='claude-account-check-') as cwd:
            try:
                with subprocess.Popen([self.binary] + args, env=env, cwd=cwd,
                                      stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True,
                                      start_new_session=True) as proc:
                    try:
                        deadline = time.monotonic() + (timeout or self.timeout)
                        while True:
                            if (self.cancel is not None and self.cancel.is_set()) or time.monotonic() >= deadline:
                                raise subprocess.TimeoutExpired(self.binary, timeout or self.timeout)
                            try:
                                output, _ = proc.communicate(input=input_text, timeout=min(0.2, max(0.001, deadline - time.monotonic())))
                                break
                            except subprocess.TimeoutExpired:
                                continue
                            finally:
                                input_text = None
                    except subprocess.TimeoutExpired:
                        self.failure = 'timeout'
                        return None, None
                    finally:
                        if proc.returncode is None:
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            proc.wait()
                data = ([json.loads(line) for line in output.splitlines() if line.strip()]
                        if 'stream-json' in args else json.loads(output))
                return proc.returncode, data
            except OSError:
                self.failure = 'unavailable'
                return None, None
            except (ValueError, UnicodeError):
                self.failure = 'invalid_response'
                return None, None

    @staticmethod
    def _status(state, source, **fields):
        return dict(state=state, source=source, checked_at=int(time.time()), **fields)

    def auth(self, profile):
        code, data = self._json(profile, ['auth', 'status', '--json'])
        if isinstance(data, dict):
            if code == 1 and data.get('loggedIn') is False:
                return self._status('needs_login', 'local')
            if code == 0 and data.get('loggedIn') is True and data.get('authMethod') == 'claude.ai':
                identity = {key: data[key] for key in ('email', 'orgId', 'orgName', 'subscriptionType', 'loginExpiresAt')
                            if isinstance(data.get(key), str)}
                return self._status('saved', 'local', **identity)
            if profile is None and code == 0 and data.get('loggedIn') is True:
                return self._status('provider', 'local')
        return self._status('unknown', 'local', reason=self.failure)

    def sessions(self, profile):
        code, data = self._json(profile, ['agents', '--json'])
        if code != 0 or not isinstance(data, list):
            return {'available': False, 'sessions': [], 'reason': self.failure}
        rows = []
        for row in data:
            if not isinstance(row, dict):
                return {'available': False, 'sessions': [], 'reason': 'invalid_response'}
            identity = row.get('id', row.get('sessionId'))
            state = row.get('state', row.get('status'))
            if not isinstance(identity, str) or not isinstance(state, str):
                return {'available': False, 'sessions': [], 'reason': 'invalid_response'}
            rows.append(dict({k: row[k] for k in ('sessionId', 'kind', 'cwd', 'startedAt')
                              if isinstance(row.get(k), (str, int, float))}, id=identity, state=state))
        return {'available': True, 'sessions': rows}

    def login(self, profile, browser=False, timeout=300, cancel=None):
        env = managed_environment(profile, self.env)
        if cancel is not None and cancel.is_set():
            return 130
        try:
            with tempfile.TemporaryDirectory(prefix='claude-account-login-') as cwd:
                if browser:
                    with subprocess.Popen([self.binary, 'auth', 'login', '--claudeai'], env=env, cwd=cwd,
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, start_new_session=True) as proc:
                        try:
                            deadline = time.monotonic() + timeout
                            while proc.poll() is None:
                                if cancel is not None and cancel.is_set():
                                    return 130
                                if time.monotonic() >= deadline:
                                    raise subprocess.TimeoutExpired(self.binary, timeout)
                                try:
                                    proc.wait(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
                                except subprocess.TimeoutExpired:
                                    pass
                            return proc.returncode
                        except (subprocess.TimeoutExpired, KeyboardInterrupt):
                            raise AccountError('Browser sign-in timed out or was interrupted; profile retained.') from None
                        finally:
                            if proc.poll() is None:
                                try:
                                    os.killpg(proc.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                proc.wait()
                return subprocess.call([self.binary, 'auth', 'login', '--claudeai'], env=env, cwd=cwd)
        except KeyboardInterrupt:
            return 130
        except OSError:
            raise AccountError('Could not start Claude for sign-in.') from None

    def check(self, profile):
        code, data = self._json(profile, [
            '-p', 'Reply with OK.', '--safe-mode', '--tools', '', '--strict-mcp-config',
            '--no-session-persistence', '--output-format', 'stream-json', '--verbose',
        ], timeout=60)
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            return self._status('unknown', 'online', reason=self.failure)
        for row in data:
            error = row.get('error')
            kind = error.get('type') if isinstance(error, dict) else error
            state = {
                'authentication_error': 'needs_login', 'authentication_failed': 'needs_login',
                'rate_limit_error': 'limited', 'rate_limit': 'limited',
                'permission_error': 'access_denied', 'billing_error': 'access_denied',
                'connection_error': 'network_error', 'api_connection_error': 'network_error',
            }.get(kind, 'unknown') if isinstance(kind, str) else 'unknown'
            if state != 'unknown':
                return self._status(state, 'online')
        if code == 0 and data and data[-1].get('type') == 'result' and (
            data[-1].get('subtype') == 'success' and data[-1].get('is_error') is False
        ):
            return self._status('verified', 'online')
        return self._status('unknown', 'online', reason=self.failure)

    def usage(self, profile):
        from herdr_claude_accounts.usage import normalize
        requests = [{'type': 'control_request', 'request_id': identity,
                     'request': {'subtype': subtype}}
                    for identity, subtype in [('init', 'initialize'), ('usage', 'get_usage')]]
        code, data = self._json(profile, [
            '-p', '--safe-mode', '--tools', '', '--strict-mcp-config', '--no-session-persistence',
            '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
        ], timeout=25, input_text=''.join(json.dumps(r) + '\n' for r in requests))
        if code == 0 and isinstance(data, list):
            for row in data:
                if not isinstance(row, dict) or row.get('type') != 'control_response':
                    continue
                response = row.get('response')
                if isinstance(response, dict) and response.get('request_id') == 'usage':
                    if response.get('subtype') == 'success':
                        return normalize(response.get('response'))
                    return self._status('unavailable', 'claude', reason='unsupported', windows=[])
        return self._status('unavailable', 'claude', reason=self.failure, windows=[])
