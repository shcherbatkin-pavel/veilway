# Veilway

Veilway is a self-hosted VPN service with Direct and fail-closed multi-hop
routing and a public profile/restart control plane.

The prototype has one operator and a baseline client set consisting of an
Ubuntu laptop and an iPhone. The operator can explicitly provision additional
device profiles through the server PKI after the approved handover; local PKI
tools are limited to bootstrap before that handover. The web application provides
Google registration/sign-in with ADMIN and USER roles. Veilway
deploys OpenVPN 2.6 containers to new, dedicated Ubuntu 24.04 LTS virtual
machines in Yandex Cloud and AWS. The implemented modes provide direct internet
access through either provider and a Yandex-ingress, AWS-egress multi-hop path.
The web application manages restart operations for the two dedicated VPN VMs
for ADMIN. The profile API supports issuing profiles, assigning USER owners,
repeatable owner-only downloads and local revocation through an isolated PKI.
Signed CRL delivery and the [browser profile cabinet](docs/profile-panel.md) are implemented in the
[profile management plan](docs/profile-management-plan.md).

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
- [Public restart control plane ADR](docs/adr/0004-public-restart-control-plane.md)
- [Google identities and authoritative server PKI ADR](docs/adr/0005-google-profiles-and-server-pki.md)
- [Migration of existing VPN profiles without reissuing](docs/legacy-profile-migration.md)
- [Profile panel rollout, backup and recovery](docs/profile-rollout.md)
- [Security and live acceptance](docs/profile-security-acceptance.md)
- [Restart control plane guide](docs/control-plane.md)
- [Isolated PKI service and manual import](docs/pki-service.md)
- [Profile API and durable PKI jobs](docs/profile-api.md)
- [Google sign-in and profile management plan](docs/profile-management-plan.md)
- [Deployment guide](docs/deployment.md)
- [Safe VM audit guide](docs/audit.md)

Run `./scripts/check.sh` for local static checks and
`./scripts/container-smoke.sh` after building the pinned application image.
`./scripts/pki-smoke.py` exercises the six baseline identities, additional
sanitized device identifiers, the transit identity, remote-update behavior,
and revocation using temporary test-only key material.
The check runner never starts containers or installs tooling. It validates
shell/Python syntax, local expiry and parser contracts, Terraform and Ansible
syntax when available, Compose exposure, frontend checks when
local dependencies exist, and the Git ignore policy. Credential scanning reads
only changed Git-visible regular files and prints filenames rather than values.

PKI functions live in `scripts/lib/pki/`; source files only define functions.
The `veilway-pki` CLI retains the protected storage paths, interactive CA
passphrase and existing command contract. AWS diagnostics execute commands in
the shell entrypoint and parse synthetic-testable input in `scripts/lib/`.

Container smoke tests are separate operator-invoked commands. Backend tests use
mocked providers and cannot restart real VMs. Refactoring these tools does not
authorize deployment or any change to cloud resources.

`./scripts/test-pki-service.sh --build` tests the autonomous PKI with temporary
synthetic material, concurrent/replayed jobs, crash recovery and UID isolation.

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

Signed CRL delivery and dedicated-node cutover: [docs/crl-delivery.md](docs/crl-delivery.md).
