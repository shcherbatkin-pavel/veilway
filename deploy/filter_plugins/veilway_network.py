"""Small, dependency-free network filters used by the Veilway playbook."""

from ipaddress import ip_network

from ansible.errors import AnsibleFilterError


def _network(value):
    try:
        return ip_network(value, strict=True)
    except ValueError as exc:
        raise AnsibleFilterError(f"invalid canonical CIDR: {value}") from exc


def network_address(value):
    return str(_network(value).network_address)


def network_netmask(value):
    return str(_network(value).netmask)


def network_first_host(value):
    network = _network(value)
    try:
        return str(next(network.hosts()))
    except StopIteration as exc:
        raise AnsibleFilterError(f"CIDR has no usable host address: {value}") from exc


def network_host(value, offset):
    network = _network(value)
    position = int(offset)
    if position < 1:
        raise AnsibleFilterError("host offset must be at least one")
    try:
        address = network.network_address + position
    except ValueError as exc:
        raise AnsibleFilterError(f"host offset is outside CIDR: {value}") from exc
    if address not in network or (
        network.version == 4 and address == network.broadcast_address
    ):
        raise AnsibleFilterError(f"host offset is outside usable CIDR: {value}")
    return str(address)


def network_overlaps(value, other):
    first = _network(value)
    second = _network(other)
    if first.version != second.version:
        return False
    return first.overlaps(second)


def network_overlaps_any(value, others):
    return any(network_overlaps(value, other) for other in others)


def networks_are_version(values, version):
    expected_version = int(version)
    return all(_network(value).version == expected_version for value in values)


class FilterModule:
    def filters(self):
        return {
            "veilway_network_address": network_address,
            "veilway_network_netmask": network_netmask,
            "veilway_network_first_host": network_first_host,
            "veilway_network_host": network_host,
            "veilway_network_overlaps": network_overlaps,
            "veilway_network_overlaps_any": network_overlaps_any,
            "veilway_networks_are_version": networks_are_version,
        }
