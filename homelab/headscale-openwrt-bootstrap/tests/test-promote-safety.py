#!/usr/bin/env python3
"""Exercise ordering and ambiguous-failure safety without touching real hosts."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / 'headscale-promote.py'
spec = importlib.util.spec_from_file_location('promote', FILE)
promote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promote)


class ClientSmoke(unittest.TestCase):
    @patch.object(promote, 'read_json_command')
    @patch.object(promote, 'ssh')
    def test_derp_requires_handshake_with_map_hostname(self, ssh, read_json):
        host_name = 'hs.example.test'
        target_ip = '192.0.2.2'
        state = [{'ControlURL': 'https://' + host_name}, {'BackendState': 'Running'},
                 {'Regions': {'7': {}, '999': {'Nodes': [
                     {'IPv4': target_ip, 'Name': '999', 'HostName': host_name}]}}}]
        with self.subTest('connection established'):
            read_json.side_effect = iter(state)
            ssh.side_effect = ['200', json.dumps({'Info': [
                f'Successfully established a DERP connection with node "{host_name}"']}), 'TSMP pong']
            self.assertTrue(promote.client_smoke('router', host_name, target_ip, '100.64.0.1'))
        with self.subTest('only HTTP health passes'):
            read_json.side_effect = iter(state)
            ssh.side_effect = ['200', '{"Info":null}']
            self.assertFalse(promote.client_smoke('router', host_name, target_ip, '100.64.0.1'))


class PromotionSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'token').write_text('test')
        (root / 'passphrase').write_text('test')
        self.args = SimpleNamespace(source='old', target='new', source_ip='192.0.2.1',
            target_ip='192.0.2.2', domain='hs.example.test', source_legacy='old.example.test',
            target_legacy='new.example.test', cf_zone='example.test', cf_token_file=root / 'token',
            passphrase_file=root / 'passphrase', backup_dir=root / 'backups', routers=['router'],
            preflight_only=False, probes=['router=100.64.0.1'])
        self.events = []
        self.fail_target = False
        self.target_phase = 'writer-may-have-started'
        self.target_unreachable = False
        self.fail_seal = False
        self.fail_clients = False
        fake_dr = SimpleNamespace(private_file=lambda p: Path(p))
        patches = [
            patch('importlib.machinery.SourceFileLoader.load_module', return_value=fake_dr),
            patch.object(promote, 'cf_record', return_value=('zone', {'content': '192.0.2.1'})),
            patch.object(promote, 'node_digest', return_value=(6, 'digest')),
            patch.object(promote, 'ssh', side_effect=lambda host, cmd, **kw:
                         'inactive' if 'hs-dr-derp-recovery' in cmd else 'identical headplane release'),
            patch.object(promote, 'reject_duplicate_derp'),
            patch.object(promote, 'snapshot', side_effect=self.snapshot),
            patch.object(promote, 'replicate', side_effect=lambda *a: self.events.append(('replicate',))),
            patch.object(promote, 'prepare', side_effect=self.prepare),
            patch.object(promote, 'command', return_value=json.dumps({'isolated_restore': 'passed', 'nodes_and_approved_routes': 6})),
            patch.object(promote, 'helper', side_effect=self.helper),
            patch.object(promote, 'target_state', side_effect=self.state),
            patch.object(promote, 'dns_change', side_effect=lambda *a: self.events.append(('dns', a[-1]))),
            patch.object(promote, 'client_smoke', return_value=True),
            patch.object(promote, 'wait_for_clients', side_effect=self.wait_for_clients),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def snapshot(self, host, args):
        self.events.append(('snapshot', host))
        return '/private/' + host + '.gpg'

    def prepare(self, host, archive, args):
        self.events.append(('prepare', host))
        return '/var/lib/headscale-dr/staged/snapshot-0123456789abcdef'

    def wait_for_clients(self, args, target_ip, probes):
        self.events.append(('clients', target_ip))
        if self.fail_clients:
            raise RuntimeError('client recovery not verified')

    def helper(self, host, path, args, **kwargs):
        if path == promote.FENCE:
            action = args[0]
            self.events.append(('fence', host, action))
            if action == 'seal' and self.fail_seal:
                raise RuntimeError('seal response lost')
            return {'headscale_enabled': 'masked', 'headscale_active': 'inactive', 'sealed': action in ('seal', 'status')}
        if path == promote.ACTIVATE:
            self.events.append(('activate', host))
            if host == 'new' and self.fail_target:
                raise RuntimeError('activation response lost')
            return {'phase': 'active'}
        self.events.append(('relay', host))
        return {'stable_relay': 200}

    def state(self, host, op):
        self.events.append(('fence-target-and-cancel', host))
        if self.target_unreachable:
            raise RuntimeError('target unreachable')
        return {'phase': self.target_phase}

    def test_success_fences_before_start_and_dns(self):
        promote.promote(self.args)
        e = self.events
        self.assertLess(e.index(('fence', 'old', 'seal')), e.index(('activate', 'new')))
        self.assertLess(e.index(('activate', 'new')), e.index(('relay', 'old')))
        client_check = e.index(('clients', '192.0.2.2'))
        dns_update = e.index(('dns', '192.0.2.2'))
        self.assertLess(e.index(('relay', 'old')), client_check)
        self.assertLess(client_check, dns_update)
        self.assertGreater(e.index(('clients', '192.0.2.2'), client_check + 1), dns_update)

    def test_failed_client_recovery_rolls_back_before_dns_cutover(self):
        self.fail_clients = True
        with self.assertRaisesRegex(RuntimeError, 'safe rollback completed'):
            promote.promote(self.args)
        self.assertNotIn(('dns', '192.0.2.2'), self.events)
        self.assertLess(self.events.index(('fence-target-and-cancel', 'new')),
                        self.events.index(('activate', 'old')))

    def test_unhealthy_wireguard_prevents_fencing(self):
        with patch.object(promote, 'client_smoke', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'preflight WireGuard'):
                promote.promote(self.args)
        self.assertFalse(any(event[0] == 'fence' for event in self.events))

    def test_duplicate_derp_prevents_fencing(self):
        with patch.object(promote, 'reject_duplicate_derp', side_effect=RuntimeError('duplicate DERP')):
            with self.assertRaisesRegex(RuntimeError, 'duplicate DERP'):
                promote.promote(self.args)
        self.assertFalse(any(event[0] == 'fence' for event in self.events))

    def test_ambiguous_started_target_is_fenced_and_snapshotted_before_source_start(self):
        self.fail_target = True
        with self.assertRaisesRegex(RuntimeError, 'safe rollback completed'):
            promote.promote(self.args)
        e = self.events
        fence = e.index(('fence-target-and-cancel', 'new'))
        snapshot = e.index(('snapshot', 'new'), fence)
        self.assertLess(fence, snapshot)
        self.assertLess(snapshot, e.index(('activate', 'old')))
        self.assertNotIn(('fence', 'old', 'release-unwritten'), e)

    def test_unreachable_target_never_releases_old_writer(self):
        self.fail_target = True
        self.target_unreachable = True
        with self.assertRaisesRegex(RuntimeError, 'automatic rollback could not be verified'):
            promote.promote(self.args)
        self.assertNotIn(('activate', 'old'), self.events)
        self.assertNotIn(('fence', 'old', 'release-unwritten'), self.events)

    def test_proven_never_started_target_allows_release_after_cancellation(self):
        self.fail_target = True
        self.target_phase = 'installed-but-never-started'
        with self.assertRaisesRegex(RuntimeError, 'safe rollback completed'):
            promote.promote(self.args)
        self.assertLess(self.events.index(('fence-target-and-cancel', 'new')),
                        self.events.index(('fence', 'old', 'release-unwritten')))

    def test_seal_response_loss_checks_state_before_release(self):
        self.fail_seal = True
        with self.assertRaisesRegex(RuntimeError, 'source restored before target start'):
            promote.promote(self.args)
        self.assertLess(self.events.index(('fence', 'old', 'status')),
                        self.events.index(('fence', 'old', 'release-unwritten')))
        self.assertNotIn(('activate', 'new'), self.events)


if __name__ == '__main__':
    unittest.main()
