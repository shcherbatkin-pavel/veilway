"""Pure stdin parsers for the AWS data-channel diagnostic. No host access."""

import sys

def validate_ipv4():
    import ipaddress
    import sys

    try:
        address = ipaddress.ip_address(sys.stdin.read().strip())
    except ValueError:
        raise SystemExit(1)
    raise SystemExit(0 if address.version == 4 else 1)


def active_tunnel_count():
    import json
    import sys

    links = json.load(sys.stdin)
    count = 0
    for link in links:
        name = link.get("ifname", "")
        kind = link.get("linkinfo", {}).get("info_kind", "")
        if kind in {"tun", "wireguard"} or name.startswith(("tun", "tap", "wg")):
            count += 1
    print(count)


def active_tunnel_interface():
    import json
    import sys

    links = json.load(sys.stdin)
    matches = []
    for link in links:
        name = link.get("ifname", "")
        kind = link.get("linkinfo", {}).get("info_kind", "")
        if kind in {"tun", "wireguard"} or name.startswith(("tun", "tap", "wg")):
            matches.append(name)
    if len(matches) != 1 or not matches[0]:
        raise SystemExit(1)
    print(matches[0])


def vpn_interface_for_cidr():
    import ipaddress
    import json
    import sys

    network = ipaddress.ip_network(sys.argv[1])
    links = json.load(sys.stdin)
    matches = set()
    for link in links:
        for address in link.get("addr_info", []):
            if address.get("family") != "inet" or not address.get("local"):
                continue
            if ipaddress.ip_address(address["local"]) in network:
                matches.add(link.get("ifname", ""))
    if len(matches) != 1 or "" in matches:
        raise SystemExit(1)
    print(matches.pop())


def client_data_path():
    import json
    import sys

    try:
        links = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        raise SystemExit(1)
    if len(links) != 1:
        raise SystemExit(1)
    kind = links[0].get("linkinfo", {}).get("info_kind", "")
    if kind in {"ovpn", "ovpn-dco"}:
        print("dco")
    elif kind == "tun" or sys.argv[1] == "classic":
        print("classic-tun")
    else:
        print("unknown")


def route_interface_for_destination():
    import json
    import sys

    try:
        routes = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        raise SystemExit(1)
    if len(routes) != 1 or not routes[0].get("dev"):
        raise SystemExit(1)
    print(routes[0]["dev"])


def route_source_for_destination():
    import ipaddress
    import json
    import sys

    try:
        routes = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        raise SystemExit(1)
    if len(routes) != 1 or not routes[0].get("prefsrc"):
        raise SystemExit(1)
    try:
        source = ipaddress.ip_address(routes[0]["prefsrc"])
    except ValueError:
        raise SystemExit(1)
    if source.version != 4:
        raise SystemExit(1)
    print(source)


def route_fingerprint_for_destination():
    import ipaddress
    import json
    import sys

    try:
        routes = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        raise SystemExit(1)
    if len(routes) != 1:
        raise SystemExit(1)
    route = routes[0]
    device = route.get("dev", "")
    source_text = route.get("prefsrc", "")
    if not device or not source_text:
        raise SystemExit(1)
    try:
        source = ipaddress.ip_address(source_text)
    except ValueError:
        raise SystemExit(1)
    if source.version != 4:
        raise SystemExit(1)
    gateway_text = route.get("gateway", "")
    if gateway_text:
        try:
            gateway = ipaddress.ip_address(gateway_text)
        except ValueError:
            raise SystemExit(1)
        if gateway.version != 4:
            raise SystemExit(1)
    table = route.get("table", "main")
    print(json.dumps({
        "dev": device,
        "gateway": gateway_text,
        "prefsrc": source_text,
        "table": table,
    }, sort_keys=True, separators=(",", ":")))


def route_source_belongs_to_interface():
    import ipaddress
    import json
    import sys

    source = ipaddress.ip_address(sys.argv[1])
    vpn_network = ipaddress.ip_network(sys.argv[2])
    try:
        links = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        raise SystemExit(1)
    local_addresses = {
        ipaddress.ip_address(address["local"])
        for link in links
        for address in link.get("addr_info", [])
        if address.get("family") == "inet" and address.get("local")
    }
    raise SystemExit(0 if source in local_addresses and source not in vpn_network else 1)


def openvpn_socket_source_status():
    import ipaddress
    import sys

    endpoint = ipaddress.ip_address(sys.argv[1])
    expected_source = ipaddress.ip_address(sys.argv[2])
    expected_port = int(sys.argv[3])

    def split_socket(value):
        host, separator, port = value.rpartition(":")
        if not separator or not port.isdigit():
            return None
        try:
            return ipaddress.ip_address(host.strip("[]")), int(port)
        except ValueError:
            return None

    sources = []
    for line in sys.stdin:
        fields = line.split()
        if len(fields) < 5:
            continue
        local_socket = split_socket(fields[-2])
        peer_socket = split_socket(fields[-1])
        if local_socket is None or peer_socket != (endpoint, expected_port):
            continue
        sources.append(local_socket[0])
    if not sources:
        print("unavailable")
    elif all(source == expected_source for source in sources):
        print("passed")
    else:
        print("mismatch")


def parse_server_openvpn_ingress_counter():
    import json
    import sys

    try:
        document = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        raise SystemExit(1)
    matches = []
    for item in document.get("nftables", []):
        counter = item.get("counter")
        if not isinstance(counter, dict):
            continue
        if (
            counter.get("family") == "inet"
            and counter.get("table") == "veilway_filter"
            and counter.get("name") == sys.argv[1]
            and isinstance(counter.get("packets"), int)
        ):
            matches.append(counter["packets"])
    if len(matches) != 1:
        raise SystemExit(1)
    print(matches[0])


def udp_out_datagrams():
    lines = sys.stdin.read().splitlines()
    for index in range(len(lines) - 1):
        if not lines[index].startswith("Udp:") or not lines[index + 1].startswith("Udp:"):
            continue
        names = lines[index].split()[1:]
        values = lines[index + 1].split()[1:]
        fields = dict(zip(names, values, strict=True))
        value = fields.get("OutDatagrams", "")
        if not value.isdigit():
            raise SystemExit(1)
        print(value)
        raise SystemExit(0)
    raise SystemExit(1)


def write_state():
    import json
    import sys

    document = {
        "phase": sys.argv[1],
        "server_rx_before": int(sys.argv[2]),
        "server_tx_before": int(sys.argv[3]),
        "ingress_before": int(sys.argv[4]),
        "raw_ingress_before": int(sys.argv[5]),
        "client_tx_delta": int(sys.argv[6]),
        "udp_delta": int(sys.argv[7]),
        "client_rx_delta": int(sys.argv[8]),
        "ping_succeeded": sys.argv[9] == "true",
        "outer_route_bypassed": sys.argv[10] == "true",
        "data_path": sys.argv[11],
    }
    print(json.dumps(document, sort_keys=True))


def read_state():
    import json
    import sys

    expected = {
        "phase",
        "server_rx_before",
        "server_tx_before",
        "ingress_before",
        "raw_ingress_before",
        "client_tx_delta",
        "udp_delta",
        "client_rx_delta",
        "ping_succeeded",
        "outer_route_bypassed",
        "data_path",
    }
    try:
        document = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError):
        raise SystemExit(1)
    if not isinstance(document, dict) or set(document) != expected:
        raise SystemExit(1)
    if document["phase"] not in {"prepared", "probed"}:
        raise SystemExit(1)
    for name in (
        "server_rx_before",
        "server_tx_before",
        "ingress_before",
        "raw_ingress_before",
        "client_tx_delta",
        "udp_delta",
        "client_rx_delta",
    ):
        if not isinstance(document[name], int) or document[name] < 0:
            raise SystemExit(1)
    for name in ("ping_succeeded", "outer_route_bypassed"):
        if not isinstance(document[name], bool):
            raise SystemExit(1)
    if document["data_path"] not in {"pending", "dco", "classic-tun", "unknown"}:
        raise SystemExit(1)
    print(document["phase"])
    print(document["server_rx_before"])
    print(document["server_tx_before"])
    print(document["ingress_before"])
    print(document["raw_ingress_before"])
    print(document["client_tx_delta"])
    print(document["udp_delta"])
    print(document["client_rx_delta"])
    print(str(document["ping_succeeded"]).lower())
    print(str(document["outer_route_bypassed"]).lower())
    print(document["data_path"])


PARSERS = {
    "read_state": read_state,
    "write_state": write_state,
    "validate_ipv4": validate_ipv4,
    "active_tunnel_count": active_tunnel_count,
    "active_tunnel_interface": active_tunnel_interface,
    "vpn_interface_for_cidr": vpn_interface_for_cidr,
    "client_data_path": client_data_path,
    "route_interface_for_destination": route_interface_for_destination,
    "route_source_for_destination": route_source_for_destination,
    "route_fingerprint_for_destination": route_fingerprint_for_destination,
    "route_source_belongs_to_interface": route_source_belongs_to_interface,
    "openvpn_socket_source_status": openvpn_socket_source_status,
    "parse_server_openvpn_ingress_counter": parse_server_openvpn_ingress_counter,
    "udp_out_datagrams": udp_out_datagrams,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in PARSERS:
        print("unknown diagnostic parser", file=sys.stderr)
        return 2
    parser = PARSERS[sys.argv.pop(1)]
    parser()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
