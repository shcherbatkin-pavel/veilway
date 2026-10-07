#!/usr/bin/env python3
"""Offline handover and Ansible contracts using only temporary synthetic files."""
import fcntl
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent


class HandoverTests(unittest.TestCase):
    def test_deployment_wrapper_validates_private_inputs_without_running_ansible(self):
        with tempfile.TemporaryDirectory(prefix="veilway-input-contract-") as directory:
            root = Path(directory)
            (root / "scripts/lib").mkdir(parents=True)
            shutil.copy(ROOT / "scripts/deploy-web-control.py", root / "scripts/deploy-web-control.py")
            shutil.copy(ROOT / "scripts/lib/operator_validation.py", root / "scripts/lib/operator_validation.py")
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text(".env\ninventory.yml\n")
            (root / "inventory.yml").write_text("all: {}\n")
            constants = ast.parse((ROOT / "scripts/deploy-web-control.py").read_text())
            minimums = next(ast.literal_eval(node.value) for node in constants.body
                            if isinstance(node, ast.Assign) and any(isinstance(name, ast.Name) and name.id == "MINIMUM_LENGTHS" for name in node.targets))
            values = {key: "synthetic-" + key.lower() + "-" + "x" * size for key, size in minimums.items()}
            values.update(PKI_YC_ENDPOINT="192.0.2.10", PKI_AWS_ENDPOINT="192.0.2.20", ADMIN_GOOGLE_EMAIL="operator@example.test",
                          PKI_CA_PASSPHRASE="synthetic-ca-pass18")
            configuration = root / ".env"
            configuration.write_text("\n".join(key + "=" + value for key, value in values.items()))
            configuration.chmod(0o600)
            base = ["python3", str(root / "scripts/deploy-web-control.py")]
            for component in ("web", "heartbeat", "crl"):
                result = subprocess.run(base + [component, "--inventory", str(root / "inventory.yml")], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("no host was contacted", result.stdout)
                self.assertNotIn(values["GOOGLE_CLIENT_SECRET"], result.stdout + result.stderr)
            configuration.write_text("\n".join(key + "=" + ("" if key == "PKI_CA_PASSPHRASE" else value) for key, value in values.items()))
            result = subprocess.run(base + ["web"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("PKI_CA_PASSPHRASE", result.stderr)
            configuration.write_text("synthetic-sensitive-unknown-key=value")
            result = subprocess.run(base + ["web"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("synthetic-sensitive-unknown-key", result.stderr)
            configuration.write_text((ROOT / ".env.example").read_text())
            result = subprocess.run(base + ["web"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("unreplaced placeholder", result.stderr)

    def test_handover_waits_for_writer_and_blocks_all_local_changes(self):
        with tempfile.TemporaryDirectory(prefix="veilway-handover-") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copy(ROOT / "scripts/veilway-pki", root / "scripts/veilway-pki")
            shutil.copytree(ROOT / "scripts/lib/pki", root / "scripts/lib/pki")
            pki = root / "secrets/pki"
            pki.mkdir(parents=True, mode=0o700)
            command = ["bash", str(root / "scripts/veilway-pki")]
            with (pki / ".writer.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                child = subprocess.Popen(command + ["handover", "--confirm-server-managed"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    time.sleep(0.15)
                    self.assertIsNone(child.poll())
                    self.assertFalse((pki / ".server-managed").exists())
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    stdout, stderr = child.communicate(timeout=5)
                    self.assertEqual(child.returncode, 0, stderr.decode())
                finally:
                    if child.poll() is None:
                        child.kill(); child.wait()
            self.assertEqual((pki / ".server-managed").stat().st_mode & 0o777, 0o600)
            for arguments in (["init"], ["server", "create", "--mode", "yc-direct"],
                              ["transit", "create"], ["profile", "create", "--device", "test", "--mode", "aws-direct"],
                              ["profile", "revoke", "--name", "test-aws-direct"],
                              ["profile", "update-remote", "--mode", "aws-direct"]):
                result = subprocess.run(command + arguments, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("CA is server-managed", result.stderr)
            self.assertFalse((pki / "ca").exists())
            self.assertEqual(subprocess.run(command + ["--help"], capture_output=True).returncode, 0)

    @unittest.skipUnless(shutil.which("ansible-playbook"), "Ansible is not installed")
    def test_real_ansible_refuses_legacy_and_skips_stale_crl(self):
        with tempfile.TemporaryDirectory(prefix="veilway-rollout-") as directory:
            root = Path(directory)
            local, remote = root / "pki", root / "agent"
            local.mkdir(); remote.mkdir()
            (local / ".server-managed").write_text("server-managed\n")
            (remote / "crl.pem").write_text("synthetic-current-crl")
            (local / "crl.pem").write_text("synthetic-stale-crl")
            (local / "ca").mkdir()
            (local / "ca/ca.crt").write_text("synthetic-public-ca")
            endpoint = local / "endpoints/aws-direct"
            endpoint.mkdir(parents=True)
            for name in ("server.crt", "server.key", "tls-crypt-v2-server.key"):
                (endpoint / name).write_text("synthetic-test-fixture")
            destination = root / "config/pki"
            destination.mkdir(parents=True)
            (destination / "crl.pem").write_text("synthetic-preserved-crl")
            variables = dict(veilway_pki_root=str(local), veilway_crl_state_root=str(remote),
                             veilway_config_root=str(root / "config"), veilway_mode="aws-direct",
                             veilway_multihop_enabled=False, veilway_crl_agent_managed=False)
            tasks = [{"ansible.builtin.include_tasks": str(ROOT / "deploy/roles/veilway_direct/tasks/authority.yml")},
                     {"ansible.builtin.include_tasks": str(ROOT / "deploy/roles/veilway_direct/tasks/pki.yml")},
                     {"ansible.builtin.assert": {"that": [
                         "(veilway_public_pki_copy.results | selectattr('item.dest', 'equalto', 'crl.pem') | first).skipped | bool"]}}]
            playbook = root / "playbook.json"
            environment = {**os.environ, "ANSIBLE_LOCAL_TEMP": str(root / "ansible"), "ANSIBLE_NOCOLOR": "1"}
            for managed, expected in ((False, 2), (True, 0)):
                variables["veilway_crl_agent_managed"] = managed
                playbook.write_text(json.dumps([dict(hosts="localhost", gather_facts=False, vars=variables, tasks=tasks)]))
                result = subprocess.run(["ansible-playbook", "--inventory", "localhost,", "--connection", "local",
                                         "--check", str(playbook)], env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertEqual((destination / "crl.pem").read_text(), "synthetic-preserved-crl")
            # Even a different workstation without the local marker cannot
            # switch a bootstrapped node back to stale-CRL deployment.
            (local / ".server-managed").unlink()
            variables["veilway_crl_agent_managed"] = False
            playbook.write_text(json.dumps([dict(hosts="localhost", gather_facts=False, vars=variables, tasks=tasks)]))
            result = subprocess.run(["ansible-playbook", "--inventory", "localhost,", "--connection", "local",
                                     "--check", str(playbook)], env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
