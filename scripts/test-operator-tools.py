#!/usr/bin/env python3
"""Offline CLI and parser contracts using synthetic input only."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from lib.operator_validation import ipv4_host_cidrs, protected_file_mode, rule_port

ROOT = Path(__file__).resolve().parent.parent
PARSER = ROOT / "scripts/lib/aws_diagnostic_parsers.py"


class ParserTests(unittest.TestCase):
    def run_parser(self, name, data, *args, code=0):
        result = subprocess.run(
            [sys.executable, str(PARSER), name, *args],
            input=data if isinstance(data, str) else json.dumps(data),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, code, result.stderr)
        return result.stdout.strip()

    def test_ipv4(self):
        self.run_parser("validate_ipv4", "192.0.2.1")
        for value in ("bad", "2001:db8::1"):
            self.run_parser("validate_ipv4", value, code=1)

    def test_tunnels(self):
        links = [{"ifname": "eth0"}, {"ifname": "tun0", "linkinfo": {"info_kind": "tun"}}]
        self.assertEqual(self.run_parser("active_tunnel_count", links), "1")
        self.assertEqual(self.run_parser("active_tunnel_interface", links), "tun0")
        self.run_parser("active_tunnel_interface", [], code=1)
        self.run_parser("active_tunnel_interface", links + [{"ifname": "wg0"}], code=1)

    def test_addresses(self):
        links = [{"ifname": "tun0", "addr_info": [{"family": "inet", "local": "10.242.20.2"}]}]
        self.assertEqual(self.run_parser("vpn_interface_for_cidr", links, "10.242.20.0/24"), "tun0")
        self.run_parser("vpn_interface_for_cidr", [], "10.242.20.0/24", code=1)
        self.run_parser("route_source_belongs_to_interface", links, "10.242.20.2", "10.242.20.0/24", code=1)
        public = [{"addr_info": [{"family": "inet", "local": "192.0.2.1"}]}]
        self.run_parser("route_source_belongs_to_interface", public, "192.0.2.1", "10.242.20.0/24")
        self.run_parser("route_source_belongs_to_interface", public, "192.0.2.2", "10.242.20.0/24", code=1)

    def test_data_path(self):
        for kind, expected in (("ovpn", "dco"), ("ovpn-dco", "dco"), ("tun", "classic-tun"), ("other", "unknown")):
            self.assertEqual(self.run_parser("client_data_path", [{"linkinfo": {"info_kind": kind}}], "not-classic"), expected)
        self.assertEqual(self.run_parser("client_data_path", [{}], "classic"), "classic-tun")
        self.run_parser("client_data_path", [], "classic", code=1)

    def test_routes(self):
        route = [{"dev": "eth0", "prefsrc": "192.0.2.1", "gateway": "192.0.2.254"}]
        self.assertEqual(self.run_parser("route_interface_for_destination", route), "eth0")
        self.assertEqual(self.run_parser("route_source_for_destination", route), "192.0.2.1")
        fingerprint = json.loads(self.run_parser("route_fingerprint_for_destination", route))
        self.assertEqual(fingerprint, {**route[0], "table": "main"})
        for name in ("route_interface_for_destination", "route_source_for_destination", "route_fingerprint_for_destination"):
            for data in ("invalid-json", [], [{}], route * 2):
                self.run_parser(name, data, code=1)
        self.run_parser("route_source_for_destination", [{"prefsrc": "2001:db8::1"}], code=1)

    def test_sockets(self):
        row = "ESTAB 0 0 192.0.2.1:45000 198.51.100.1:1194\n"
        args = ("198.51.100.1", "192.0.2.1", "1194")
        self.assertEqual(self.run_parser("openvpn_socket_source_status", row, *args), "passed")
        self.assertEqual(self.run_parser("openvpn_socket_source_status", row.replace("192.0.2.1", "192.0.2.2"), *args), "mismatch")
        self.assertEqual(self.run_parser("openvpn_socket_source_status", "malformed\n", *args), "unavailable")

    def test_counters(self):
        counter = {"family": "inet", "table": "veilway_filter", "name": "openvpn_ingress", "packets": 42}
        self.assertEqual(self.run_parser("parse_server_openvpn_ingress_counter", {"nftables": [{"counter": counter}]}, "openvpn_ingress"), "42")
        for items in ([], [{"counter": counter}] * 2, [{"counter": {**counter, "table": "other"}}]):
            self.run_parser("parse_server_openvpn_ingress_counter", {"nftables": items}, "openvpn_ingress", code=1)
        self.assertEqual(self.run_parser("udp_out_datagrams", "Udp: InDatagrams OutDatagrams\nUdp: 10 42\n"), "42")
        self.run_parser("udp_out_datagrams", "Udp: InDatagrams OutDatagrams\nUdp: 10 bad\n", code=1)

    def test_state_roundtrip_and_invalid_documents(self):
        args = ("prepared", "1", "2", "3", "4", "5", "6", "7", "false", "true", "pending")
        state = json.loads(self.run_parser("write_state", "", *args))
        self.assertEqual(self.run_parser("read_state", state).splitlines(), list(args))
        for invalid in ("bad-json", [], {}, {**state, "phase": "invalid"}, {**state, "client_tx_delta": -1}, {**state, "ping_succeeded": "false"}, {**state, "extra": 1}):
            self.run_parser("read_state", invalid, code=1)

    def test_unknown_parser(self):
        self.run_parser("missing", "", code=2)


class OperatorContracts(unittest.TestCase):
    def test_cli_rejections_do_not_require_private_inputs(self):
        temporary = tempfile.TemporaryDirectory(prefix="veilway-cli-contract-")
        self.addCleanup(temporary.cleanup)
        isolated_scripts = Path(temporary.name) / "scripts"
        isolated_scripts.mkdir()
        shutil.copy(ROOT / "scripts/veilway-pki", isolated_scripts / "veilway-pki")
        shutil.copytree(ROOT / "scripts/lib/pki", isolated_scripts / "lib/pki")
        for command, args, code, message in (
            ("veilway-pki", [], 2, "Usage:"),
            ("veilway-pki", ["profile", "create", "--device", "../unsafe", "--mode", "yc-direct"], 1, "device identifier"),
            ("veilway-pki", ["server", "create", "--mode", "unknown"], 1, "mode must"),
            ("diagnose-aws-data-channel", ["invalid"], 1, "Usage:"),
            ("review-aws-direct-plan.py", ["--invalid"], 2, "usage:"),
            ("review-yandex-multihop-plan.py", ["--invalid"], 2, "usage:"),
            ("test-control-plane.sh", [], 2, "Usage:"),
        ):
            with self.subTest(command=command, args=args):
                executable = [sys.executable] if command.endswith(".py") else []
                script = (isolated_scripts if command == "veilway-pki" else ROOT / "scripts") / command
                result = subprocess.run([*executable, str(script), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn(message, result.stdout + result.stderr)

    def test_modules_only_define_functions(self):
        modules = sorted((ROOT / "scripts/lib/pki").glob("*.sh"))
        with tempfile.TemporaryDirectory(prefix="veilway-source-test.") as directory:
            result = subprocess.run(
                ["bash", "-c", 'set -euo pipefail; for module in "$@"; do source "$module"; done', "source-test", *map(str, modules)],
                cwd=directory, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_validation(self):
        self.assertTrue(ipv4_host_cidrs(["192.0.2.1/32", "198.51.100.1/32"], 2))
        for values in (["192.0.2.1/24"], ["2001:db8::1/128"], ["bad"], []):
            self.assertFalse(ipv4_host_cidrs(values, 1))
        self.assertEqual(rule_port({"port": 1195}), 1195)
        self.assertEqual(rule_port({"from_port": 1195, "to_port": 1195}), 1195)
        self.assertIsNone(rule_port({"from_port": 1, "to_port": 2}))
        with tempfile.TemporaryDirectory(prefix="veilway-mode-test.") as directory:
            path = Path(directory) / "synthetic"
            path.touch(mode=0o600)
            self.assertEqual(protected_file_mode(path), 0o600)
            os.chmod(path, 0o644)
            self.assertEqual(protected_file_mode(path), 0o644)


if __name__ == "__main__":
    unittest.main()
