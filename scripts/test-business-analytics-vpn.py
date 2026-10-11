#!/usr/bin/env python3
"""Real OpenVPN accounting acceptance in a disposable private network namespace."""
import argparse
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent.parent
IMAGE='veilway-analytics-vpn-test:local'
PROGRAM=r"""
import json,os,stat,subprocess,sys,tempfile,time,uuid
from pathlib import Path
sys.path.insert(0,'/usr/local/lib')
from veilway_session_accounting import Store

def run(args):
 subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def wait(predicate,process,seconds=15):
 deadline=time.monotonic()+seconds
 last_error='no matching event'
 while time.monotonic()<deadline:
  try:
   value=predicate()
   if value:return value
  except (OSError,ValueError) as error:last_error=type(error).__name__+': '+str(error)
  if process.poll() is not None:
   output=process.communicate()[0]
   raise RuntimeError('synthetic OpenVPN exited: '+(output.decode() if output else 'no output'))
  time.sleep(.05)
 raise RuntimeError('synthetic OpenVPN stage timed out: '+last_error)
def read_accounting(function):
 os.seteuid(900)
 try:return function()
 finally:os.seteuid(0)

os.umask(0o077)
Path('/dev/net').mkdir(exist_ok=True)
if not Path('/dev/net/tun').exists():os.mknod('/dev/net/tun',stat.S_IFCHR|0o600,os.makedev(10,200))
for root in ('/var/lib/veilway-analytics','/run/veilway-analytics'):
 path=Path(root)/'aws-direct';path.mkdir(parents=True,mode=0o700);os.chown(path,900,900)
status=Path('/run/veilway-analytics/aws-direct/status.tsv');status.touch(mode=0o600);os.chown(status,900,900)
with tempfile.TemporaryDirectory() as temporary:
 root=Path(temporary)
 run(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-days','1','-subj','/CN=Synthetic-accounting-CA','-keyout',str(root/'ca.key'),'-out',str(root/'ca.crt')])
 def identity(name,number,usage,label):
  run(['openssl','req','-new','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-subj','/CN='+name,'-keyout',str(root/(label+'.key')),'-out',str(root/(label+'.csr'))])
  (root/'extensions').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyAgreement\nextendedKeyUsage='+usage+'\n')
  run(['openssl','x509','-req','-in',str(root/(label+'.csr')),'-CA',str(root/'ca.crt'),'-CAkey',str(root/'ca.key'),'-set_serial',str(number),'-days','1','-extfile',str(root/'extensions'),'-out',str(root/(label+'.crt'))])
 identity('synthetic-server',100,'serverAuth','server')
 names=[str(uuid.uuid4()),'legacy-fixture']
 for i,name in enumerate(names):identity(name,122+i,'clientAuth','client'+str(i))
 hook='"/usr/local/bin/veilway-session-hook aws-direct"'
 config=f'''dev tun-server
proto udp
disable-dco
local 127.0.0.1
port 1194
topology subnet
server 10.254.1.0 255.255.255.0
ca {root}/ca.crt
cert {root}/server.crt
key {root}/server.key
dh none
tls-version-min 1.3
data-ciphers AES-128-GCM
remote-cert-tls client
persist-key
persist-tun
user openvpn
group openvpn
script-security 2
up {hook}
down {hook}
client-connect {hook}
client-disconnect {hook}
status /run/veilway-analytics/aws-direct/status.tsv 1
status-version 3
keepalive 1 10
verb 0
'''
 (root/'server.conf').write_text(config)
 server=subprocess.Popen(['/usr/sbin/openvpn','--config',str(root/'server.conf')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 try:
  wait(lambda:Path('/run/veilway-analytics/aws-direct/epoch').exists(),server)
  store=Store('aws-direct')
  for i,name in enumerate(names):
   conf=f'''client
dev tun-client
proto udp
disable-dco
remote 127.0.0.1 1194
nobind
ca {root}/ca.crt
cert {root}/client{i}.crt
key {root}/client{i}.key
remote-cert-tls server
verify-x509-name synthetic-server name
tls-version-min 1.3
data-ciphers AES-128-GCM
route-noexec
explicit-exit-notify 1
verb 0
'''
   path=root/f'client{i}.conf';path.write_text(conf)
   client=subprocess.Popen(['/usr/sbin/openvpn','--config',str(path)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
   try:
    def connected():
     return next((e for e in read_accounting(lambda:store.pending(500)) if e['kind']=='start'),None)
    start=wait(connected,client)
    assert start['certificate_serial']==format(122+i,'X'), 'certificate serial missing from real callback'
    def current_snapshot():
     report=read_accounting(lambda:store.snapshot())
     return report if report['connection_count']==1 else None
    report=wait(current_snapshot,client)
    assert report['connection_count']==1 and report['sessions'][0]['certificate_serial']==format(122+i,'X'), json.dumps({'event':start,'snapshot':report})
    assert '127.0.0.1' not in json.dumps(report)
   except Exception:
    client.terminate(); output=client.communicate(timeout=8)[0]
    print(output.decode(),file=sys.stderr)
    raise
   finally:
    if client.poll() is None:client.terminate();client.wait(timeout=8)
   def ended():
    return next((e for e in read_accounting(lambda:store.pending(500)) if e['kind']=='end' and e['session_id']==start['session_id']),None)
   end=wait(ended,server)
   assert end['observed_at']>=end['started_at'] and end['bytes_received']>=0
   assert end['profile_id']==(name if i==0 else None)
   read_accounting(lambda:store.acknowledge([e['id'] for e in store.pending(500)]))
  print('Real OpenVPN: UUID and legacy certificates map by serial; status snapshots and disconnect counters pass')
 except Exception:
  server.terminate(); output=server.communicate(timeout=8)[0]
  print(output.decode(),file=sys.stderr)
  raise
 finally:
  if server.poll() is None:server.terminate();server.wait(timeout=8)
"""


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',action='store_true')
    args=parser.parse_args()
    if args.build:
        subprocess.run(['docker','build','--file','deploy/image/Dockerfile','--tag',IMAGE,'deploy/image'],cwd=ROOT,check=True)
    command=['docker','run','--rm','--interactive','--network','none','--read-only','--cap-drop','ALL',
        '--cap-add','NET_ADMIN','--cap-add','MKNOD','--cap-add','CHOWN','--cap-add','DAC_OVERRIDE','--cap-add','SETUID','--cap-add','SETGID','--cap-add','KILL',
        '--device-cgroup-rule','c 10:200 rwm','--security-opt','no-new-privileges:true',
        '--tmpfs','/tmp:rw,noexec,nosuid,size=32m','--tmpfs','/run/veilway-analytics:rw,noexec,nosuid,size=16m',
        '--tmpfs','/var/lib/veilway-analytics:rw,noexec,nosuid,size=32m','--entrypoint','/usr/bin/python3',IMAGE,'-']
    result=subprocess.run(command,input=PROGRAM.encode(),cwd=ROOT,check=False)
    raise SystemExit(result.returncode)


if __name__=='__main__':main()
