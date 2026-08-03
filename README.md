# Veilway

Veilway is an experimental self-hosted VPN service with a web interface and
optional multi-hop routing.

The prototype targets one user with an Ubuntu laptop and an iPhone. Its planned
connection modes are direct internet access through Yandex Cloud or AWS and a
multi-hop path through both providers. Access to provider-private networks is
out of scope, and the future web panel will be reachable only through an SSH
tunnel.

## Current iteration

This repository currently provides the project requirements, roadmap, and a
read-only Ubuntu VM audit script. It does **not** deploy or configure OpenVPN,
WireGuard, Ansible, a web panel, or multi-hop routing. An existing OpenVPN
Access Server must remain unchanged.

- [Prototype requirements](docs/requirements.md)
- [Development roadmap](docs/roadmap.md)
- [Safe VM audit guide](docs/audit.md)

## Sensitive audit data

Audit reports contain network topology, addresses, routes, service state, and
firewall rules. They are stored under the Git-ignored `audit-results/`
directory, must be reviewed before copying, and must never be committed,
published, or attached to a public issue or pull request.

## License

Veilway is licensed under the [MIT License](LICENSE).
