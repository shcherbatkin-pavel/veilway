# Veilway prototype requirements

## Purpose

Veilway will provide self-hosted, internet-only VPN connectivity through two
Ubuntu virtual machines: one in Yandex Cloud and one in AWS. The prototype is
for a single operator and is not a multi-tenant service.

## Users and clients

- One user.
- Two client devices: an Ubuntu laptop and an iPhone.
- Client profiles and credentials must be generated and handled as secrets.

## Planned connection modes

- `yc-direct`: the client exits to the internet through the Yandex Cloud VM.
- `aws-direct`: the client exits to the internet through the AWS VM.
- `yc-aws-multihop`: the client enters through Yandex Cloud and exits through AWS.

All modes provide internet access only. They must not grant access to private
AWS or Yandex Cloud networks.

## Management and coexistence

- The future web panel will bind to a non-public interface and be accessed
  through an operator-created SSH tunnel.
- The existing OpenVPN Access Server must not be modified, stopped, restarted,
  removed, or disrupted while Veilway is being designed and developed.
- New networking behavior must be designed to avoid route, port, address-pool,
  DNS, and firewall conflicts with the existing service.

## Security requirements

- The repository is public; no private keys, certificates, passwords, tokens,
  client profiles, audit reports, or unredacted infrastructure exports may be
  committed.
- Infrastructure inspection is read-only unless a later task explicitly
  authorizes a narrowly scoped change.
- Audit tooling must not connect to hosts automatically or transmit collected
  data.
- Audit output must remain local, have restrictive filesystem permissions, and
  be excluded from Git.
- Privileged diagnostics must be optional, clearly disclosed, and read-only.

## First-iteration deliverables

- Contributor safety rules and public prototype documentation.
- A development roadmap.
- A manually invoked, read-only Ubuntu VM audit script.
- Instructions for collecting, reviewing, and handling audit results safely.

## First-iteration non-goals

- Deploying, configuring, migrating, or removing OpenVPN or WireGuard.
- Adding Ansible, Terraform, or other infrastructure automation.
- Implementing the web panel or its API.
- Implementing direct or multi-hop packet forwarding.
- Generating client profiles or changing client devices.
- Changing VM services, routes, firewall rules, packages, or cloud resources.
