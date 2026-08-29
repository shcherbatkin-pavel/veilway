# Veilway

Veilway is a self-hosted VPN service with Direct and fail-closed multi-hop
routing and a planned private web interface.

The prototype targets one user with an Ubuntu laptop and an iPhone. It deploys
OpenVPN 2.6 containers to new, dedicated Ubuntu 24.04 LTS virtual machines in
Yandex Cloud and AWS. The implemented modes provide direct internet access
through either provider and a Yandex-ingress, AWS-egress multi-hop path. The
private web panel remains a later phase.

## Current iteration

This repository contains the greenfield architecture, independent Terraform
stacks for both clouds, an Ansible deployment, Docker Compose configuration,
and local PKI/profile tooling. Applying infrastructure or connecting to a host
is always a separate, explicit operator action. The existing OpenVPN Access
Server runs on old infrastructure outside the Veilway target and must remain
unchanged.

- [Prototype requirements](docs/requirements.md)
- [Development roadmap](docs/roadmap.md)
- [Greenfield architecture ADR](docs/adr/0001-greenfield-direct-vpn.md)
- [Rejected AWS UDP/443 trial ADR](docs/adr/0002-aws-direct-udp-443.md)
- [Fail-closed multi-hop ADR](docs/adr/0003-yc-aws-fail-closed-multihop.md)
- [Deployment guide](docs/deployment.md)
- [Safe VM audit guide](docs/audit.md)

Run `./scripts/check.sh` for local static checks and
`./scripts/container-smoke.sh` after building the pinned application image.
`./scripts/pki-smoke.py` exercises all six profile identities, the transit
identity, remote-update behavior, and revocation using temporary test-only key
material.

## Safety status

The repository does not contain cloud credentials, Terraform state, PKI private
keys, client profiles, host inventory, or audit reports. The checked-in
configuration is inert until an operator supplies local inputs and explicitly
runs Terraform and Ansible. Do not point the deployment inventory at the old
VMs.

## Sensitive audit data

Audit reports contain network topology, addresses, routes, service state, and
firewall rules. They are stored under the Git-ignored `audit-results/`
directory, must be reviewed before copying, and must never be committed,
published, or attached to a public issue or pull request.

## License

Veilway is licensed under the [MIT License](LICENSE).
