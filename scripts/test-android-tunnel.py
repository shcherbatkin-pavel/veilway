#!/usr/bin/env python3
"""Run the JNI/TUN acceptance test against a disposable localhost OpenVPN server.

Requires an already running, dedicated emulator and built debug/test APKs.
Generates only synthetic PKI; never reads production profiles or VPN configuration.
"""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import re
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "veilway/openvpn:2.6-ubuntu24.04"


def run(command, quiet=False, capture=False):
    return subprocess.run(command, check=True, text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--serial", default="emulator-5556")
    args = parser.parse_args()
    if not args.serial.startswith("emulator-"):
        parser.error("Only a dedicated local emulator is allowed")
    os.umask(0o077)
    adb = [str(args.sdk_root / "platform-tools/adb"), "-s", args.serial]
    name = "veilway-android-fixture-" + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix="veilway-android-synthetic-") as directory:
        path = Path(directory)
        def openssl(*command):
            run(["docker", "run", "--rm", "--pull", "never", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                 "--mount", f"type=bind,src={directory},dst=/fixture", "--workdir", "/fixture", "--entrypoint", "openssl", IMAGE, *command], quiet=True)
        openssl("req", "-x509", "-newkey", "rsa:2048", "-noenc", "-keyout", "ca.key", "-out", "ca.crt", "-days", "1", "-subj", "/CN=Veilway Synthetic CA")
        for identity, usage in (("server", "serverAuth"), ("client", "clientAuth")):
            (path / "extension").write_text("extendedKeyUsage=" + usage + "\n")
            openssl("req", "-newkey", "rsa:2048", "-noenc", "-keyout", identity + ".key", "-out", identity + ".csr", "-subj", "/CN=synthetic-" + identity)
            openssl("x509", "-req", "-in", identity + ".csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-out", identity + ".crt", "-days", "1", "-extfile", "extension")
        def openvpn(*command):
            run(["docker", "run", "--rm", "--pull", "never", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                 "--mount", f"type=bind,src={directory},dst=/fixture", "--workdir", "/fixture", "--entrypoint", "openvpn", IMAGE, *command], quiet=True)
        openvpn("--genkey", "tls-crypt-v2-server", "tls-server.key")
        openvpn("--tls-crypt-v2", "tls-server.key", "--genkey", "tls-crypt-v2-client", "tls-client.key")
        (path / "server.conf").write_text("""port 1194
proto udp4
dev tun
topology subnet
server 10.243.99.0 255.255.255.0
server-ipv6 fd12:feed:99::/64
ca /fixture/ca.crt
cert /fixture/server.crt
key /fixture/server.key
dh none
tls-crypt-v2 /fixture/tls-server.key
tls-version-min 1.3
data-ciphers CHACHA20-POLY1305:AES-256-GCM:AES-128-GCM
allow-compression no
push "redirect-gateway def1 ipv6"
push "dhcp-option DNS 10.243.99.1"
keepalive 1 4
verb 0
log /dev/null
""")
        (path / "unbound.conf").write_text("""server:
  interface: 10.243.99.1
  access-control: 10.243.99.0/24 allow
  username: ""
  chroot: ""
  directory: /fixture
  pidfile: /tmp/unbound.pid
  use-syslog: no
  logfile: /dev/null
  verbosity: 0
  local-zone: "veilway.test." static
  local-data: "fixture.veilway.test. IN A 10.243.99.1"
""")
        material = """client
dev tun
proto udp4
remote 10.0.2.2 18080
nobind
persist-key
persist-tun
remote-cert-tls server
verify-x509-name synthetic-server name
tls-version-min 1.3
allow-compression no
data-ciphers CHACHA20-POLY1305:AES-256-GCM:AES-128-GCM
auth SHA256
verb 0
"""
        for tag, filename in (("ca", "ca.crt"), ("cert", "client.crt"), ("key", "client.key"), ("tls-crypt-v2", "tls-client.key")):
            material += f"<{tag}>\n" + (path / filename).read_text() + f"</{tag}>\n"
        server = None
        try:
            run(["docker", "run", "--detach", "--rm", "--pull", "never", "--name", name,
                "--cap-add", "NET_ADMIN", "--device", "/dev/net/tun", "--sysctl", "net.ipv6.conf.all.disable_ipv6=0",
                "--publish", "127.0.0.1:18080:1194/udp", "--mount", f"type=bind,src={directory},dst=/fixture,readonly",
                "--entrypoint", "/bin/sh", IMAGE, "-c", "openvpn --config /fixture/server.conf --daemon; exec unbound -d -c /fixture/unbound.conf"], quiet=True)
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_GET(self):
                    if self.path == "/stop":
                        run(["docker", "pause", name], quiet=True)
                    elif self.path == "/start":
                        run(["docker", "unpause", name], quiet=True)
                    elif self.path != "/direct":
                        self.send_error(404); return
                    self.send_response(200); self.end_headers(); self.wfile.write(b"synthetic-direct")
            server = ThreadingHTTPServer(("127.0.0.1", 18081), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            time.sleep(2)
            run([*adb, "install", "-r", str(ROOT / "android/app/build/outputs/apk/debug/app-debug.apk")], quiet=True)
            run([*adb, "install", "-r", str(ROOT / "android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk")], quiet=True)
            run([*adb, "shell", "appops", "set", "ru.veilway.android", "ACTIVATE_VPN", "allow"], quiet=True)
            run([*adb, "shell", "am", "start", "-n", "ru.veilway.android/.FixtureActivity"], quiet=True)
            # Do not echo the synthetic private material to the terminal.
            run([*adb, "shell", "am", "broadcast", "-n", "ru.veilway.android/.FixtureReceiver", "--es", "fixture", base64.b64encode(material.encode()).decode()], quiet=True)
            response = run([*adb, "shell", "am", "instrument", "-w", "-r", "-e", "class", "ru.veilway.android.NativeTunnelTest,ru.veilway.android.SecretStoreTest",
                "ru.veilway.android.test/androidx.test.runner.AndroidJUnitRunner"], capture=True)
            # Only controlled test assertions, never VPN logs or profile material.
            print(response.stdout)
            if not re.search(r"OK \(2 tests\)", response.stdout):
                raise SystemExit("Android tunnel acceptance failed")
        finally:
            if server:
                server.shutdown(); server.server_close()
            subprocess.run(["docker", "unpause", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "stop", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
