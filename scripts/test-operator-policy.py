#!/usr/bin/env python3
"""Plan and inventory policy tests use synthetic dictionaries and temporary files only."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib.aws_plan_review import evaluate_plan as aws_review, EXPECTED_RESOURCES as AWS_RESOURCES
from lib.yandex_plan_review import evaluate_plan as yc_review, EXPECTED_RESOURCES as YC_RESOURCES
from lib.inventory import validate_inventory, render_inventory, InventoryError

ROOT = Path(__file__).resolve().parent.parent


def aws_plan():
    rule = lambda protocol, port, cidrs: dict(protocol=protocol, from_port=port, to_port=port, cidr_blocks=cidrs, ipv6_cidr_blocks=[])
    values = {
        'aws_vpc.vpn': dict(assign_generated_ipv6_cidr_block=True),
        'aws_subnet.vpn': dict(availability_zone='eu-west-2a', assign_ipv6_address_on_creation=True, map_public_ip_on_launch=False),
        'aws_route_table.vpn': dict(route=[dict(cidr_block='0.0.0.0/0'), dict(ipv6_cidr_block='::/0')]),
        'aws_instance.vpn': dict(id='synthetic-vm', source_dest_check=False, associate_public_ip_address=False,
            ipv6_address_count=1, instance_type='t3.small', metadata_options=[dict(http_endpoint='enabled', http_tokens='required',
            http_put_response_hop_limit=1, http_protocol_ipv6='disabled', instance_metadata_tags='disabled')],
            root_block_device=[dict(encrypted=True, volume_type='gp3', volume_size=20)]),
        'aws_security_group.vpn': dict(ingress=[rule('tcp',22,['192.0.2.10/32','198.51.100.10/32']),
            rule('udp',1194,['0.0.0.0/0']), rule('icmp',-1,['0.0.0.0/0']),
            dict(protocol='icmpv6',from_port=-1,to_port=-1,cidr_blocks=[],ipv6_cidr_blocks=['::/0'])],
            egress=[dict(protocol='-1',cidr_blocks=['0.0.0.0/0'],ipv6_cidr_blocks=['::/0'])]),
        'aws_eip.vpn': dict(domain='vpc', allocation_id='synthetic-allocation', public_ip='192.0.2.1'),
    }
    return dict(resource_changes=[dict(address=address,mode='managed',change=dict(actions=['create'],before=None,after=values.get(address,{}))) for address in sorted(AWS_RESOURCES)])


def yc_plan(disable=False):
    ingress=[dict(protocol='TCP',port=22,v4_cidr_blocks=['192.0.2.10/32','198.51.100.10/32']),
        dict(protocol='UDP',port=1194,v4_cidr_blocks=['0.0.0.0/0']),dict(protocol='ICMP',v4_cidr_blocks=['0.0.0.0/0'])]
    multihop=dict(protocol='UDP',port=1195,v4_cidr_blocks=['0.0.0.0/0'])
    values={'yandex_vpc_security_group.vpn':dict(ingress=ingress if disable else ingress+[multihop],egress=[dict(protocol='ANY',v4_cidr_blocks=['0.0.0.0/0'])]),
        'yandex_compute_instance.vpn':dict(id='synthetic-vm'), 'yandex_vpc_address.vpn':dict(id='synthetic-ip')}
    plan=dict(resource_changes=[dict(address=address,mode='managed',change=dict(actions=['update'] if address=='yandex_vpc_security_group.vpn' else ['no-op'],
        before=copy.deepcopy(values.get(address,{})),after=values.get(address,{}))) for address in sorted(YC_RESOURCES)])
    sg=next(row for row in plan['resource_changes'] if row['address']=='yandex_vpc_security_group.vpn')
    sg['change']['before']['ingress']=ingress+[multihop] if disable else ingress
    return plan


def aws_existing_plan(mode):
    plan=aws_plan()
    for row in plan['resource_changes']:
        row['change']['before']=copy.deepcopy(row['change']['after'])
        row['change']['actions']=['no-op']
    rows={row['address']:row['change'] for row in plan['resource_changes']}
    if mode=='recovery':
        rows['aws_instance.vpn']['actions']=['delete','create']
        rows['aws_eip.vpn']['actions']=['update']
    else:
        sg=rows['aws_security_group.vpn'];sg['actions']=['update']
        if mode=='port_rollback':
            old_rule=sg['before']['ingress'][1]
            old_rule['from_port']=443;old_rule['to_port']=443
        else:
            transit=dict(protocol='udp',from_port=1196,to_port=1196,cidr_blocks=['192.0.2.1/32'],ipv6_cidr_blocks=[])
            sg['after' if mode=='enable_multihop' else 'before']['ingress'].append(transit)
    return plan


def inventory_inputs(multihop=False):
    states=[]
    endpoints={'yc_direct_endpoint':'192.0.2.1','aws_direct_endpoint':'198.51.100.1'}
    for cloud,prefix,endpoint in [('yandex','yandex','192.0.2.1'),('aws','aws','198.51.100.1')]:
        values=dict(public_ipv4=endpoint, ansible_host_alias=dict(name='yc-direct' if cloud=='yandex' else 'aws-direct',user='ubuntu'),
            vpc_cidr='10.240.0.0/24',vpn_ipv4_cidr='10.241.0.0/24',vpc_ipv6_cidr='fd12:3456:1::/64',
            vpn_ipv6_cidr='fd12:3456:2::/64',public_ipv6='2001:4860::1234',multihop_ipv4_cidr='10.242.0.0/24',
            transit_ipv4_cidr='10.243.0.0/29',multihop_ipv6_cidr='fd12:3456:3::/64',transit_ipv6_cidr='fd12:3456:4::/64')
        cidr_key='v4_cidr_blocks' if cloud=='yandex' else 'cidr_blocks'
        rules=[dict(description='SSH from explicitly trusted operator networks',**{cidr_key:['192.0.2.10/32']})]
        if multihop:
            rules.append(dict(protocol='UDP' if cloud=='yandex' else 'udp',port=1195 if cloud=='yandex' else 1196,
                **{cidr_key:['0.0.0.0/0'] if cloud=='yandex' else ['192.0.2.1/32']}))
        states.append(dict(outputs={name:dict(value=value) for name,value in values.items()},resources=[dict(mode='managed',type=prefix+'_vpc_security_group' if cloud=='yandex' else 'aws_security_group',name='vpn',instances=[dict(attributes=dict(ingress=rules))])]))
    known={'yc':Path('/synthetic/known_hosts.yc-direct'),'aws':Path('/synthetic/known_hosts.aws-direct')}
    return (*states,endpoints,known)


class PlanPolicyTests(unittest.TestCase):
    def test_existing_aws_operations_preserve_vm_and_allocation_contracts(self):
        for mode in ['recovery','port_rollback','enable_multihop','disable_multihop']:
            plan=aws_existing_plan(mode)
            checks=aws_review(plan,**{mode:True})
            self.assertTrue(all(checks.values()),(mode,checks))
            result=subprocess.run([sys.executable,str(ROOT/'scripts/review-aws-direct-plan.py'),'--'+mode.replace('_','-')],
                input=json.dumps(plan),text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,''.join(f'{name}: passed\n' for name in checks))
            plan['resource_changes'].append(dict(address='unexpected.resource',mode='managed',change=dict(actions=['no-op'],after={})))
            self.assertFalse(aws_review(plan,**{mode:True})['resource allowlist'])

    def test_valid_plans_are_unchanged_and_cli_renders_only_named_checks(self):
        for script,plan,review,args in [('review-aws-direct-plan.py',aws_plan(),aws_review,[]),
            ('review-yandex-multihop-plan.py',yc_plan(),yc_review,[]),
            ('review-yandex-multihop-plan.py',yc_plan(True),lambda p:yc_review(p,disable_multihop=True),['--disable-multihop'])]:
            original=copy.deepcopy(plan); checks=review(plan)
            self.assertTrue(all(checks.values()),checks)
            self.assertEqual(plan,original)
            result=subprocess.run([sys.executable,str(ROOT/'scripts'/script),*args],input=json.dumps(plan),text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,''.join(f'{name}: passed\n' for name in checks))
            self.assertNotIn('synthetic-vm',result.stdout)

    def test_unexpected_resource_replacement_and_public_ssh_are_rejected(self):
        for review,fixture in [(aws_review,aws_plan),(yc_review,yc_plan)]:
            plan=fixture();plan['resource_changes'].append(dict(address='unexpected.resource',mode='managed',change=dict(actions=['create'],after={})))
            self.assertFalse(review(plan)['resource allowlist'])
            plan=fixture();vm=next(row for row in plan['resource_changes'] if 'instance.vpn' in row['address'])
            vm['change']['actions']=['delete','create']
            self.assertFalse(all(review(plan).values()))
            plan=fixture();sg=next(row for row in plan['resource_changes'] if 'security_group.vpn' in row['address'])
            rule=sg['change']['after']['ingress'][0]
            rule['cidr_blocks' if review==aws_review else 'v4_cidr_blocks']=['0.0.0.0/0']
            self.assertFalse(review(plan)['SSH restricted to two IPv4 /32'])


class InventoryPolicyTests(unittest.TestCase):
    def test_direct_and_multihop_rendering_preserve_hardening_without_mutating_inputs(self):
        for enabled in [False,True]:
            inputs=inventory_inputs(enabled);before=copy.deepcopy(inputs)
            hosts=validate_inventory(*inputs,enable_multihop=enabled)
            text=render_inventory(hosts)
            self.assertEqual(inputs,before)
            self.assertTrue(text.startswith('---\nall:\n  children:\n    direct_vpn:\n      hosts:\n'))
            self.assertEqual(text.count('StrictHostKeyChecking=yes'),2)
            self.assertEqual(text.count('veilway_openvpn_port: 1194'),2)
            self.assertEqual(text.count(f'veilway_multihop_enabled: {str(enabled).lower()}'),2)
            self.assertEqual(text.count('veilway_transit_port: 1196'),2 if enabled else 0)
            self.assertIn('veilway_enable_ipv6: false',text)
            self.assertIn('veilway_enable_ipv6: true',text)

    def test_endpoint_alias_network_and_multihop_mismatches_are_rejected(self):
        inputs=inventory_inputs();inputs[2]['yc_direct_endpoint']='192.0.2.2'
        with self.assertRaisesRegex(InventoryError,'endpoint configuration'):validate_inventory(*inputs)
        inputs=inventory_inputs();inputs[0]['outputs']['ansible_host_alias']['value']['user']='root'
        with self.assertRaisesRegex(InventoryError,'alias'):validate_inventory(*inputs)
        inputs=inventory_inputs();inputs[1]['outputs']['vpn_ipv4_cidr']['value']='10.241.0.1/24'
        with self.assertRaises(InventoryError):validate_inventory(*inputs)
        inputs=inventory_inputs(True);inputs[1]['outputs']['transit_ipv4_cidr']['value']='10.244.0.0/29'
        with self.assertRaisesRegex(InventoryError,'outputs differ'):validate_inventory(*inputs,enable_multihop=True)
        inputs=inventory_inputs(True);inputs[1]['resources'][0]['instances'][0]['attributes']['ingress'][1]['cidr_blocks']=['0.0.0.0/0']
        with self.assertRaisesRegex(InventoryError,'restrict'):validate_inventory(*inputs,enable_multihop=True)

    def test_protected_file_and_atomic_writer_preserve_existing_output_on_failure(self):
        spec=importlib.util.spec_from_file_location('inventory_cli',ROOT/'scripts/render-inventory.py')
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory(prefix='veilway-inventory-policy-') as directory:
            path=Path(directory)/'inventory.yml';path.write_text('synthetic-before');path.chmod(0o600)
            cli.require_protected_file(path)
            with patch.object(cli.os,'replace',side_effect=OSError('synthetic-failure')):
                with self.assertRaises(OSError):cli.write_inventory(path,'synthetic-after')
            self.assertEqual(path.read_text(),'synthetic-before')
            self.assertEqual(list(Path(directory).iterdir()),[path])
            cli.write_inventory(path,'synthetic-after')
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            link=Path(directory)/'link';link.symlink_to(path)
            with self.assertRaises(InventoryError):cli.require_protected_file(link)
            path.chmod(0o644)
            with self.assertRaises(InventoryError):cli.require_protected_file(path)


if __name__=='__main__':unittest.main()
