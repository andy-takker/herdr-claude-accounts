from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from accounts import Store
from usage import cached_usage, normalize
from manager import parser, run


class UsageTests(unittest.TestCase):
    def test_persistent_ttl_and_manual_refresh_only_one_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            account = store.create('Work')['id']
            auth = {'state':'saved', 'email':'a@example.com', 'orgId':'org'}
            class Client:
                binary = '/bin/claude'
                calls = []
                def auth(self, profile): return auth
                def usage(self, profile):
                    self.calls.append(profile)
                    return {'state':'available', 'checked_at':1000, 'windows':[
                        {'label':'Weekly', 'used_percent':len(self.calls), 'resets_at':5000}]}
            cli = Client()
            with patch('usage.time.time', return_value=1000):
                cached_usage(store, cli, None, auth)
                cached_usage(store, cli, account, auth)
            with patch('usage.time.time', return_value=1299):
                cached_usage(Store(tmp), cli, None, auth)
                cached_usage(Store(tmp), cli, account, auth)
                self.assertEqual(len(cli.calls), 2)
                with patch('manager.client', return_value=cli):
                    result = run(parser().parse_args(['--root',tmp,'refresh-usage',account]))
                self.assertEqual(result['windows'][0]['used_percent'], 3)
                self.assertEqual(cli.calls, [None, store.profile(account), store.profile(account)])
                cached_usage(Store(tmp), cli, None, auth)
                self.assertEqual(len(cli.calls), 3)
            with patch('usage.time.time', return_value=1300):
                cached_usage(Store(tmp), cli, None, auth)
                self.assertEqual(cli.calls[-1], None)
                self.assertEqual(len(cli.calls), 4)

    def test_cancelled_poll_restores_previous_cache_and_allows_retry(self):
        for seeded in (False, True):
            with self.subTest(previous_cache=seeded), tempfile.TemporaryDirectory() as tmp:
                store = Store(tmp)
                auth = {'state': 'saved', 'email': 'fixture@example.test'}
                class Client:
                    binary = '/bin/claude'
                    cancel = threading.Event()
                    def usage(self, profile):
                        return {'state': 'available', 'checked_at': 1000, 'windows': [
                            {'label': 'Weekly', 'used_percent': 50, 'resets_at': 5000}]}
                cli = Client()
                if seeded:
                    with patch('usage.time.time', return_value=1000):
                        cached_usage(store, cli, None, auth)
                previous = store._load('usage.json', {})
                def cancelled(profile):
                    cli.cancel.set()
                    return {'state': 'unavailable', 'windows': [], 'reason': 'timeout'}
                with patch('usage.time.time', return_value=1301), patch.object(cli, 'usage', side_effect=cancelled):
                    cached_usage(store, cli, None, auth)
                self.assertEqual(store._load('usage.json', {}), previous)
                cli.cancel.clear()
                with patch('usage.time.time', return_value=1301), patch.object(cli, 'usage', wraps=cli.usage) as probe:
                    self.assertEqual(cached_usage(store, cli, None, auth)['state'], 'available')
                    self.assertEqual(probe.call_count, 1)

    def test_cancelled_poll_does_not_rollback_newer_manual_refresh_in_same_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            auth = {'state': 'saved', 'email': 'fixture@example.test'}
            class Fresh:
                binary = '/bin/claude'
                def usage(self, profile):
                    return {'state': 'available', 'checked_at': 1000, 'windows': [
                        {'label': 'Weekly', 'used_percent': 55, 'resets_at': 5000}]}
            class Cancelled:
                binary = '/bin/claude'
                cancel = threading.Event()
                def usage(self, profile):
                    cached_usage(store, Fresh(), None, auth, force=True)
                    self.cancel.set()
                    return {'state': 'unavailable', 'windows': [], 'reason': 'timeout'}
            with patch('usage.time.time', return_value=1000):
                cached_usage(store, Cancelled(), None, auth)
                result = cached_usage(store, Fresh(), None, auth)
            self.assertEqual(result['state'], 'available')
            self.assertEqual(result['windows'][0]['used_percent'], 55)

    def test_malformed_limits_fail_without_crashing_or_reporting_free_quota(self):
        for values in ({'group':[],'percent':10}, {'group':{},'percent':10},
                       {'group':'weekly','percent':10**1000}, {'group':'weekly','percent':float('nan')}):
            result=normalize({'rate_limits_available':True,'rate_limits':{'limits':[values]}})
            self.assertEqual(result['state'],'unavailable')
            self.assertEqual(result['reason'],'invalid_response')
            self.assertEqual(result['windows'],[])

    def test_cache_is_identity_bound_and_retains_stale_data_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            class Client:
                binary='/bin/claude'
                calls=0
                def usage(self,profile):
                    self.calls+=1
                    return {'state':'available','checked_at':1000,'windows':[
                        {'label':'Weekly','used_percent':93,'resets_at':5000}]}
            cli=Client(); auth={'state':'saved','email':'a@example.com','orgId':'org'}
            with patch('usage.time.time',return_value=1000):
                a=cached_usage(store,cli,None,auth)
                cached_usage(store,cli,None,auth)
                self.assertEqual(cli.calls,1)
                self.assertEqual(a['windows'][0]['used_percent'],93)
            with patch('usage.time.time',return_value=1301), patch.object(cli,'usage',return_value={
                    'state':'unavailable','reason':'timeout','checked_at':1301,'windows':[]}):
                stale=cached_usage(store,cli,None,auth)
                self.assertEqual(stale['state'],'stale')
                self.assertEqual(stale['checked_at'],1000)
                self.assertEqual(stale['reason'],'timeout')
                changed=cached_usage(store,cli,None,dict(auth,email='b@example.com'))
                self.assertEqual(changed['state'],'unavailable')
                self.assertEqual(changed['windows'],[])
            self.assertEqual(cached_usage(store,cli,None,{'state':'needs_login'})['windows'],[])
