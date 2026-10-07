#!/usr/bin/env python3
"""Check the built panel proxy on isolated loopback; no ACME or real backend."""
import json
import subprocess
import time
import uuid


def main():
    name = "veilway-proxy-test-" + uuid.uuid4().hex[:12]
    image = "veilway-control-web:stage7"
    adapted = subprocess.run(
        ["docker", "run", "--rm", "--pull", "never", "--network", "none",
         "--read-only", "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE",
         "--security-opt", "no-new-privileges:true",
         "--entrypoint", "caddy", image, "adapt", "--config", "/etc/caddy/Caddyfile"],
        capture_output=True, text=True, check=True,
    )
    config = json.loads(adapted.stdout)
    # Validate the production parser's result, rather than matching source text.
    assert config["admin"]["disabled"] is True
    assert config["logging"]["logs"]["default"]["encoder"]["fields"]["request"]["filter"] == "delete"
    started = False
    try:
        subprocess.run(
            ["docker", "run", "--detach", "--rm", "--pull", "never", "--name", name,
             "--network", "none", "--read-only", "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE",
             "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp:rw,nosuid,size=16m",
             "--tmpfs", "/config:rw,nosuid,size=8m", "--tmpfs", "/data:rw,nosuid,size=8m",
             "--entrypoint", "/bin/sh", image, "-c",
             "sed 's|^veilway.ru {|http://127.0.0.1:8080 {|' /etc/caddy/Caddyfile > /tmp/Caddyfile; "
             "exec caddy run --config /tmp/Caddyfile"],
            capture_output=True, text=True, check=True,
        )
        started = True
        response = None
        for _ in range(50):
            response = subprocess.run(
                ["docker", "run", "--rm", "--pull", "never", "--network", "container:" + name,
                 "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                 "--entrypoint", "python", "veilway-profile-panel-test:stage7", "-c",
                 "import http.client,json; c=http.client.HTTPConnection('127.0.0.1',8080,timeout=2); "
                 "c.request('GET','/api/v1/auth/session',headers={'X-CSRF-Token':'synthetic-proxy-csrf-canary',"
                 "'Cookie':'__Host-veilway_session=synthetic-proxy-session-canary'}); r=c.getresponse(); "
                 "print(json.dumps(dict(status=r.status,headers=dict(r.getheaders()),body=r.read().decode())))"],
                capture_output=True, text=True,
            )
            if response.returncode == 0:
                break
            time.sleep(0.1)
        assert response.returncode == 0, "isolated proxy client could not connect"
        result = json.loads(response.stdout)
        headers = {key.lower(): value for key, value in result["headers"].items()}
        assert result["status"] == 502
        assert result["body"] == "Service unavailable"
        assert headers["cache-control"] == "no-store"
        assert headers["pragma"] == "no-cache"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["x-content-type-options"] == "nosniff"
        subprocess.run(
            ["docker", "run", "--rm", "--pull", "never", "--network", "container:" + name,
             "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
             "--entrypoint", "python", "veilway-profile-panel-test:stage7", "-c",
             "import http.client; c=http.client.HTTPConnection('127.0.0.1',8080,timeout=2); "
             "c.request('GET','/api/v1/auth/google/callback?code=synthetic-proxy-code-canary&state=synthetic-proxy-state-canary'); "
             "r=c.getresponse(); assert r.status==502; r.read()"],
            capture_output=True, text=True, check=True,
        )
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, check=True)
        assert "canary" not in logs.stdout + logs.stderr, "synthetic request secrets leaked to proxy logs"
        print("Production Caddy config, non-cacheable gateway failure and log redaction passed")
    finally:
        if started:
            subprocess.run(["docker", "stop", "--timeout", "5", name],
                           capture_output=True, check=True)


if __name__ == "__main__":
    main()
