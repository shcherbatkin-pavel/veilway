#!/usr/bin/env python3
"""Local accounting fixtures only: no host, certificate or real credential access."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('veilway_session_accounting',ROOT/'deploy/image/veilway_session_accounting.py')
accounting=importlib.util.module_from_spec(spec);sys.modules[spec.name]=accounting;spec.loader.exec_module(accounting)
loader=importlib.machinery.SourceFileLoader('analytics_sender',str(ROOT/'deploy/roles/veilway_analytics/files/veilway-analytics-agent'))
spec=importlib.util.spec_from_loader(loader.name,loader);sender=importlib.util.module_from_spec(spec);loader.exec_module(sender)


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        root=Path(self.temporary.name)
        for prefix in ('state','run'):
            (root/prefix/'aws-direct').mkdir(parents=True,mode=0o700)
        self.store=accounting.Store('aws-direct',state_root=root/'state',runtime_root=root/'run')
        self.store.hook('up',{})
        (self.store.runtime/'epoch-started').write_text(accounting.stamp(999))
        self.name=str(uuid.uuid4())
        self.env=dict(common_name=self.name,time_unix='1000',tls_serial_hex_0='00:7A',
                      trusted_ip='203.0.113.9',trusted_port='4242')

    def status(self, *, observed=1010, clients=True):
        text='TIME\tfixture\t'+str(observed)+'\nHEADER\tCLIENT_LIST\tCommon Name\tReal Address\tBytes Received\tBytes Sent\tConnected Since (time_t)\n'
        if clients:
            text+='CLIENT_LIST\t'+self.name+'\t203.0.113.9:4242\t100\t200\t1000\n'
        text+='END\n'
        (self.store.runtime/'status.tsv').write_text(text)

    def test_short_session_events_exact_final_counters_and_privacy(self):
        self.store.hook('client-connect',self.env)
        self.store.hook('client-disconnect',dict(self.env,time_duration='2',bytes_received='34',bytes_sent='56'))
        events=self.store.pending(500)
        self.assertEqual([e['kind'] for e in events],['start','end'])
        self.assertEqual(events[0]['session_id'],events[1]['session_id'])
        self.assertEqual(events[1]['certificate_serial'],'7A')
        self.assertEqual(events[1]['bytes_received'],34)
        self.assertEqual(events[1]['observed_at'],accounting.stamp(1002))
        self.assertNotIn('203.0.113.9',json.dumps(events))
        self.assertNotIn('203.0.113.9',self.store.path.read_bytes().decode(errors='ignore'))

    def test_status_deltas_floating_peer_and_epoch_recovery(self):
        self.store.hook('client-connect',self.env);self.status()
        report=self.store.snapshot(now=1010)
        self.assertEqual(report['connection_count'],1)
        self.assertEqual(report['sessions'][0]['bytes_sent'],200)
        self.assertNotIn('Real Address',json.dumps(report))
        self.store.hook('client-disconnect',dict(self.env,trusted_ip='203.0.113.10',time_duration='15',bytes_received='150',bytes_sent='250'))
        self.assertEqual(self.store.pending(500)[-1]['bytes_sent'],250)
        self.store.hook('client-connect',dict(self.env,time_unix='1020'))
        self.store.hook('up',{})
        self.assertEqual(self.store.pending(500)[-1]['kind'],'interrupted')
        self.assertNotEqual(self.store.epoch(),report['epoch'])

    def test_missing_hook_recovers_current_session_and_stale_status_is_unavailable(self):
        self.status()
        report=self.store.snapshot(now=1010)
        self.assertIsNone(report['sessions'][0]['certificate_serial'])
        self.assertEqual(report['sessions'][0]['profile_id'],self.name)
        with self.assertRaises(ValueError):self.store.snapshot(now=1100)
        self.status(clients=False)
        self.assertEqual(self.store.snapshot(now=1010)['connection_count'],0)
        with self.assertRaises(ValueError):accounting.parse_status('TIME\tfixture\t1010\n')

    def test_legacy_subject_not_exported_and_lost_ack_replays(self):
        self.store.hook('client-connect',dict(self.env,common_name='legacy-fixture'))
        event=self.store.pending(500)[0]
        self.assertIsNone(event['profile_id'])
        self.assertNotIn('legacy-fixture',json.dumps(event))
        class Offline:
            def open(self,*args,**kwargs):raise OSError('synthetic-private-error')
        with self.assertRaises(OSError):sender.deliver('aws-direct',['aws-direct'],'X'*40,opener=Offline(),stores=[self.store])
        self.assertEqual(self.store.pending(500)[0]['id'],event['id'])
        class Response:
            status=204
            def __enter__(self):return self
            def __exit__(self,*args):pass
        class Online:
            def open(self,request,timeout):
                self.request=request
                return Response()
        online=Online()
        sender.deliver('aws-direct',['aws-direct'],'X'*40,opener=online,stores=[self.store])
        self.assertEqual(self.store.pending(500),[])
        self.assertTrue(online.request.full_url.endswith('/aws-direct/report'))
        self.assertIsNone(sender.NoRedirect().redirect_request(None,None,None,None,None,None))
        with self.assertRaises(ValueError):sender.deliver('aws-direct',['yc-direct'],'X'*40,stores=[self.store])

    def test_acknowledgement_does_not_resurrect_closed_status_row(self):
        import time
        now=int(time.time())
        self.env['time_unix']=str(now-10)
        self.store.hook('client-connect',self.env)
        self.store.hook('client-disconnect',dict(self.env,time_duration='5',bytes_received='10',bytes_sent='20'))
        self.store.acknowledge([e['id'] for e in self.store.pending(500)])
        (self.store.runtime/'epoch-started').write_text(accounting.stamp(now-20))
        status='TIME\tfixture\t'+str(now-1)+'\nHEADER\tCLIENT_LIST\tCommon Name\tBytes Received\tBytes Sent\tConnected Since (time_t)\nCLIENT_LIST\t'+self.name+'\t10\t20\t'+str(now-10)+'\nEND\n'
        (self.store.runtime/'status.tsv').write_text(status)
        report=self.store.snapshot(now=now)
        self.assertEqual(report['connection_count'],0)
        self.assertEqual(report['sessions'],[])

    def test_templates_enable_only_ingress_accounting(self):
        try:
            import jinja2
            import yaml
        except ImportError:
            self.skipTest('existing template tools unavailable')
        from ipaddress import ip_network
        templates=ROOT/'deploy/roles/veilway_direct/templates'
        environment=jinja2.Environment()
        environment.filters.update(veilway_network_address=lambda v:str(ip_network(v).network_address),
            veilway_network_netmask=lambda v:str(ip_network(v).netmask),
            veilway_network_host=lambda v,i:str(ip_network(v).network_address+int(i)),
            veilway_network_first_host=lambda v:str(next(ip_network(v).hosts())))
        defaults=yaml.safe_load((ROOT/'deploy/roles/veilway_direct/defaults/main.yml').read_text())
        self.assertIs(defaults['veilway_analytics_enabled'],False)
        for enabled in (False,True):
            for mode in ('aws-direct','yc-direct'):
                values=dict(defaults,veilway_analytics_enabled=enabled,veilway_mode=mode,
                    veilway_multihop_enabled=True,veilway_vpn_ipv4_cidr='10.242.10.0/24')
                services=yaml.safe_load(environment.from_string((templates/'compose.yaml.j2').read_text()).render(values))['services']
                for name,service in services.items():
                    ingress=name in ('openvpn','openvpn-multihop')
                    accounting=[v for v in service.get('volumes',[]) if 'veilway-analytics' in v]
                    self.assertEqual(len(accounting),2 if enabled and ingress else 0)
                    self.assertEqual('DAC_OVERRIDE' in service.get('cap_add',[]),enabled and ingress)
                for template in ('openvpn-server.conf.j2','openvpn-multihop-server.conf.j2'):
                    config=environment.from_string((templates/template).read_text()).render(values)
                    self.assertEqual('status-version 3' in config,enabled)
                    self.assertEqual('client-connect "/usr/local/bin/veilway-session-hook ' in config,enabled)
                for template in ('openvpn-transit-server.conf.j2','openvpn-transit-client.conf.j2'):
                    self.assertNotIn('veilway-session-hook',(templates/template).read_text())

    def test_callback_failure_is_fail_open(self):
        source=(ROOT/'deploy/image/veilway-session-hook').read_text()
        # Execute the actual callback entrypoint against an unavailable store.
        namespace={'__name__':'callback_fixture'}
        exec(compile(source,'callback_fixture','exec'),namespace)
        original_drop=accounting.drop_privileges;original_store=accounting.Store
        accounting.drop_privileges=lambda:None
        class Broken:
            def __init__(self,*args):pass
            def hook(self,*args):raise OSError('synthetic-secret')
            def loss(self):pass
        accounting.Store=Broken
        previous=sys.argv;sys.argv=['callback','aws-direct']
        try:self.assertEqual(namespace['main'](),0)
        finally:
            sys.argv=previous;accounting.drop_privileges=original_drop;accounting.Store=original_store


if __name__=='__main__':unittest.main()
