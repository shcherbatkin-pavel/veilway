# Direct VPN MVP deployment

This guide describes an explicit operator workflow for new, dedicated VMs. It
does not authorize connecting to or changing any existing host. Review every
Terraform plan and the Ansible target inventory before continuing.

The separately staged public restart panel has its own
[operator guide](control-plane.md). Its Terraform, IAM, DNS, web deployment,
heartbeat installation and first real restart each require separate approval.

## 1. Local prerequisites

- Terraform compatible with the versions declared under `infra/`.
- Ansible Core and the `community.docker` collection declared in
  `deploy/requirements.yml`.
- Docker with Compose for building the VPN image and running local PKI tools.
- OpenSSL 3.
- Python 3 with Jinja2. Terraform also uses Python for local CIDR preflight.
- Python `pexpect` is optional and used only by the isolated PKI smoke test.
- AWS and Yandex Cloud credentials supplied through their standard environment
  or local credential mechanisms. Never put them in `tfvars`.

Set a restrictive umask for the entire operator session:

```sh
umask 077
```

## 2. Create the cloud infrastructure

Work on one provider at a time. Copy the corresponding
`terraform.tfvars.example` to `terraform.tfvars`, replace placeholders, and
keep the resulting file local. Then run `terraform init`, `terraform validate`,
and `terraform plan -out=direct.tfplan` in that provider directory.

Review the plan for exactly one new network, subnet, security group, static
IPv4, and VM. For the AWS Direct plan, run the value-redacting structural
reviewer as well:

```sh
terraform -chdir=infra/aws show -json direct.tfplan \
    | python3 scripts/review-aws-direct-plan.py
```

If an empty AWS VM must be replaced during SSH bootstrap recovery, save that
operation to a separate plan and use the recovery mode. It accepts only an EC2
instance replacement plus the resulting in-place Elastic IP reassociation; all
other managed resources must be unchanged:

```sh
terraform -chdir=infra/aws show -json ssh-recovery.tfplan \
    | python3 scripts/review-aws-direct-plan.py --recovery
```

Apply only after explicit approval:

```sh
terraform apply direct.tfplan
```

Repeat separately for `infra/yandex` and `infra/aws`. The two local state files
are sensitive infrastructure data. Keep them on an encrypted disk with mode
`0600`; do not publish or commit them.

### Existing AWS endpoint: roll back the rejected UDP/443 trial

ADR 0002 records why moving `aws-direct` to UDP/443 did not restore direct data
traffic. Restore the Direct baseline with a dedicated plan. The reviewer
requires exactly one in-place security-group update from UDP/443 to UDP/1194
and rejects VM replacement or any other managed-resource change:

```sh
terraform -chdir=infra/aws plan -out=udp1194-rollback.tfplan
terraform -chdir=infra/aws show -json udp1194-rollback.tfplan \
    | python3 scripts/review-aws-direct-plan.py --port-rollback
```

Do not apply that plan until it has been reviewed and explicitly approved.
After apply, regenerate the protected inventory with the deliberate replace
flag so both Direct nodes again receive port 1194:

```sh
./scripts/render-inventory.py --replace
```

The subsequent Ansible deployment must be limited to `aws-direct`. It restores
the OpenVPN listener and nftables ingress rule to UDP/1194 and removes the
trial-only `NET_BIND_SERVICE` capability. The server and profile certificates,
keys, ciphers, VPN pools, and Yandex node do not change.

### Enable the accepted Yandex-to-AWS multi-hop infrastructure

ADR 0003 adds no VM or network. For the existing deployment, save two separate
plans that may update only the existing security groups. First plan Yandex:

```sh
terraform -chdir=infra/yandex plan -out=multihop-ingress.tfplan
terraform -chdir=infra/yandex show -json multihop-ingress.tfplan \
    | python3 scripts/review-yandex-multihop-plan.py
```

The expected summary is `0 to add, 1 to change, 0 to destroy`. The reviewer
requires public UDP/1195, keeps Direct on UDP/1194, rejects UDP/1196 on Yandex,
and requires the existing VM and static address to remain unchanged.

Set `enable_multihop = true` in both protected `terraform.tfvars` files. In the
AWS file, also set `yc_transit_source_cidr` to the Yandex static IPv4 followed
by `/32`. Do not copy that value into documentation, Git, or a command line.
Then plan AWS:

```sh
terraform -chdir=infra/aws plan -out=multihop-transit.tfplan
terraform -chdir=infra/aws show -json multihop-transit.tfplan \
    | python3 scripts/review-aws-direct-plan.py --enable-multihop
```

This plan must also report `0 to add, 1 to change, 0 to destroy`. The reviewer
requires UDP/1196 from exactly one IPv4 `/32`, keeps public Direct UDP/1194,
keeps UDP/1195 closed on AWS, and rejects VM or Elastic IP replacement. Apply
each saved plan only after its output has been reviewed and explicitly
approved. Re-run a normal plan in each root afterward and require `No changes`.

## 3. Prepare PKI and local configuration

The local PKI commands below are for bootstrap before server handover. After
handover, use [profile rollout and recovery](profile-rollout.md); the local CLI
is blocked by `.server-managed`. Keep managed CRL inventory enabled: general
node deployment must not copy the old local CRL or reopen a workstation signer.

Build the pinned application image locally:

```sh
docker build -t veilway/openvpn:2.6-ubuntu24.04 deploy/image
```

The Ubuntu base is digest-pinned. Updating that digest or the permitted
OpenVPN/Unbound package series is a reviewed dependency change and must be
followed by both static and container smoke tests.

Before generating real PKI, run the isolated container smoke test. It uses a
temporary synthetic CA under `/tmp`, runs with no external container network,
and removes its test material afterward:

```sh
./scripts/container-smoke.sh
./scripts/pki-smoke.py
```

Initialize the offline CA and endpoint keys:

```sh
./scripts/veilway-pki init
./scripts/veilway-pki server create --mode yc-direct
./scripts/veilway-pki server create --mode aws-direct
```

Copy `operator-config/endpoints.conf.example` to
`operator-config/endpoints.conf`, set the static IPv4 outputs without placing
them on a command line, and keep the file at mode `0600`.

Generate the four MVP profiles:

```sh
./scripts/veilway-pki profile create --device ubuntu --mode yc-direct
./scripts/veilway-pki profile create --device iphone --mode yc-direct
./scripts/veilway-pki profile create --device ubuntu --mode aws-direct
./scripts/veilway-pki profile create --device iphone --mode aws-direct
./scripts/verify-client-profiles --direct-only
```

For the accepted multi-hop phase, create independent endpoint and transit
identities. These commands do not replace any Direct identity:

```sh
./scripts/veilway-pki server create --mode yc-multihop-ingress
./scripts/veilway-pki server create --mode aws-transit
./scripts/veilway-pki transit create
```

The two device profiles are generated only after the multi-hop servers have
been deployed and verified:

```sh
./scripts/veilway-pki profile create --device ubuntu --mode yc-aws-multihop
./scripts/veilway-pki profile create --device iphone --mode yc-aws-multihop
./scripts/verify-client-profiles
```

Additional authorized devices use a non-personal device identifier followed by
the existing mode suffix. The identifier must contain 1 to 48 lowercase ASCII
letters or digits, with only single internal hyphens. For example:

```sh
./scripts/veilway-pki profile create --device guest-windows --mode yc-aws-multihop
./scripts/verify-client-profiles
```

Client certificates default to 365 days. Add `--valid-for 1mo`, `3mo`, `6mo`,
or `12mo` for calendar months; any positive integer number of months (`mo`)
or minutes (`m`) is accepted. Calendar arithmetic uses UTC and clamps the day
to the last day of the target month. Alternatively use
`--expires-at '2026-10-03T08:30:00+03:00'`: an ISO 8601 timestamp with seconds
and an explicit timezone. The options are mutually exclusive. Past dates and
dates beyond CA expiry are rejected before key generation or CA database writes.
The command prints the issued expiry in UTC and Moscow time. Python 3 is required.

For a manual expiry test, use a fresh device identifier:

```sh
./scripts/veilway-pki profile create --device expiry-test --mode yc-aws-multihop --valid-for 10m
```

Enter the CA passphrase locally and import the generated profile through a
trusted channel. With client and server clocks synchronized, confirm connection
before the printed expiry; after expiry, disconnect and attempt a new connection.
Certificate validation must reject the expired certificate. An existing session
may continue until a later TLS check: immediate disconnection is not guaranteed.
No server deployment or CRL update is required for certificate expiration.
The profile validator also rejects expired profiles; keep this in mind when
running a full validation after the test. Existing profiles keep their original
expiry; issue a fresh identity to obtain a different lifetime.

The validator continues to require the six baseline profiles and validates all
additional profiles, including their unique certificates, private keys, and
`tls-crypt-v2` keys. Additional identities remain operator-managed profiles;
they do not create accounts or make the service multi-tenant.

New Direct profiles use UDP/1194. To restore the two protected AWS profiles
created during the rejected UDP/443 trial without reissuing certificates or
printing embedded key material, run:

```sh
./scripts/veilway-pki profile update-remote --mode aws-direct
./scripts/verify-client-profiles --direct-only
```

The update is atomic per profile, accepts only the known UDP/443-to-UDP/1194
AWS rollback, preserves mode `0600`, and refuses symlinks, unexpected profile
identities, or files that are not ignored by Git. Re-import the updated Ubuntu
profile and replace the iPhone import only after the AWS security group and
server listener have both returned to UDP/1194.

Review profiles locally before importing them. They contain private keys.
On Ubuntu, import the profile through NetworkManager so pushed routes and DNS
are integrated with the host resolver. On iPhone, enable OpenVPN Connect's
Seamless Tunnel option before the leak tests. `persist-tun` keeps routes in
place while the OpenVPN process reconnects; deliberately stopping or removing
the client is outside that protection and restores normal device networking.
Do not run acceptance tests with another VPN or system-wide proxy active: a
nested tunnel makes the observed egress, DNS, and leak results ambiguous.

After importing each Ubuntu profile, enable persistent reconnect and give its
DNS configuration a negative priority. NetworkManager then excludes DNS from
connections with a higher numerical priority while Veilway is active:

```sh
nmcli connection modify ubuntu-yc-direct \
  vpn.persistent yes ipv4.dns-priority -50 ipv6.dns-priority -50
nmcli connection modify ubuntu-aws-direct \
  vpn.persistent yes ipv4.dns-priority -50 ipv6.dns-priority -50
nmcli connection modify ubuntu-yc-aws-multihop \
  vpn.persistent yes ipv4.dns-priority -50 ipv6.dns-priority -50
```

## 4. Configure the new VMs

Install the required Ansible collection locally:

```sh
ansible-galaxy collection install -r deploy/requirements.yml
```

Generate the ignored `deploy/inventory.yml` from the protected Terraform state,
endpoint file, and independently verified host-key files. The generator refuses
to overwrite an existing inventory unless `--replace` is explicitly supplied
and does not print addresses or CIDRs:

```sh
./scripts/render-inventory.py
```

After both multi-hop Terraform applies are complete, enable the feature in the
protected inventory explicitly:

```sh
./scripts/render-inventory.py --replace --enable-multihop
```

The generator refuses this mode unless Yandex state contains public UDP/1195,
AWS state contains UDP/1196 restricted to the Yandex endpoint `/32`, both roots
agree on the IPv4 pools, and the persistent AWS ULA outputs are present. It
also copies the exact Terraform-assigned AWS ENI IPv6 into the protected
inventory without printing it. Regenerate the inventory after any AWS instance
replacement so Ansible cannot retain the previous ENI address.

On AWS, Ansible writes that assigned address as `/128` in the protected
`90-veilway-ipv6.yaml` Netplan overlay. `systemd-networkd` is the sole owner of
router-advertisement processing for the VPC default route; kernel RA and SLAAC
are disabled explicitly, including for newly created tunnel interfaces. The
server address does not depend on DHCPv6. Netplan is validated before it is
applied; applying a changed overlay can briefly interrupt SSH and requires
explicit operator approval.

For `aws-direct`, verify and record the ED25519 host key by comparing an SSH
scan with the authenticated EC2 console output. The script reads the endpoint
from the ignored operator configuration and does not open an SSH shell:

```sh
AWS_PROFILE=veilway-terraform ./scripts/verify-aws-host-key.sh
```

Run the dedicated read-only preflight first. It validates the real hosts,
network contract, TUN device, and local PKI metadata without changing either
VM:

```sh
ansible-inventory -i deploy/inventory.yml --graph
ansible-playbook -i deploy/inventory.yml deploy/preflight.yml
```

On a clean VM, package installation predicted by check mode does not make its
binaries and directories available to later tasks. The main role therefore
ends safely after the same preflight when invoked in check mode:

```sh
ansible-playbook -i deploy/inventory.yml deploy/site.yml --check --diff
```

Deployment changes packages, sysctl, firewall, and containers on the selected
new VM. Run it only after reviewing the inventory and preview:

```sh
ansible-playbook -i deploy/inventory.yml deploy/site.yml
```

Do not enable Ansible diff output for a real deployment. A firewall or
inventory-related template change can expose operator CIDRs and other protected
infrastructure data in terminal output.

After deployment, run the dedicated read-only verifier. It checks service and
container health, hardening, nftables ownership, sysctl, deployed key modes,
and that the offline CA private key is absent from both VMs:

```sh
ansible-playbook -i deploy/inventory.yml deploy/verify.yml
```

For an IPv6-enabled AWS node, also run the read-only egress diagnostic. It
validates the exact assigned ENI address, source route, Netplan/sysctl contract,
AAAA resolution, and IPv6 HTTPS without displaying protected values:

```sh
ansible-playbook -i deploy/inventory.yml deploy/diagnose-egress.yml --limit aws-direct
```

Deploy multi-hop in cloud-egress order. Each `site.yml` command changes the
selected dedicated VM and therefore requires a separate explicit operator
approval:

```sh
ansible-playbook -i deploy/inventory.yml deploy/preflight.yml --limit aws-direct
ansible-playbook -i deploy/inventory.yml deploy/site.yml --limit aws-direct
ansible-playbook -i deploy/inventory.yml deploy/verify.yml --limit aws-direct

ansible-playbook -i deploy/inventory.yml deploy/preflight.yml --limit yc-direct
ansible-playbook -i deploy/inventory.yml deploy/site.yml --limit yc-direct
ansible-playbook -i deploy/inventory.yml deploy/verify.yml --limit yc-direct
```

On Yandex, Compose starts the transit client first and starts client ingress
only after that tunnel is healthy. The nftables ruleset never NATs the
multi-hop pool on Yandex, and source-specific policy tables retain an
unreachable default below the transit route. Re-run `deploy/site.yml` against
both hosts afterward and require `changed=0` before client acceptance.

The managed firewall keeps two named diagnostic counters for OpenVPN UDP/1194.
`openvpn_ingress_raw` has no verdict and observes packets before conntrack
validity is enforced; `openvpn_ingress` is attached to the later accept rule.
Both store only aggregate packet and byte totals on the VM and never record
addresses or payloads. The read-only AWS data-channel diagnostic compares
bounded before/after deltas without printing the totals. It uses three explicit
phases so either a trusted direct address or an operator VPN managed outside
NetworkManager can provide the SSH snapshots without another tunnel being
active during the direct AWS probe:

```sh
# Preferred when the current direct IP is in operator_cidrs:
./scripts/diagnose-aws-data-channel run
```

In this one-step mode, SSH snapshots are taken only before activation and after
the script has disconnected `aws-direct`; it does not depend on SSH remaining
reachable through a broken tunnel. SSH operations and NetworkManager changes
have bounded timeouts. The two named nftables counters are read together, and
each read-only SSH snapshot allows one bounded retry. The client compares the
selected interface, gateway, source, and routing table before and after
activation without displaying any of those values. This distinguishes packet
loss following a route change from an unchanged direct path. When available,
it also classifies whether a connected OpenVPN UDP socket uses the IPv4 source
selected for that outer route.

For an explicitly approved privileged local diagnosis, add
`--sudo-tcpdump`. Before invoking `sudo`, the script prints the bounded
operation with protected values redacted. It counts at most ten outbound UDP
packets to the AWS endpoint on port 1194 whose source matches the address
selected by the outer route and whose public OpenVPN opcode is `P_DATA_V1` or
`P_DATA_V2`. The filter masks off the three-bit key ID and does not inspect the
encrypted payload. The capture lasts at most eight seconds, uses a one-byte
snapshot, writes packet records only to `/dev/null`, and reports no addresses
or counts. Reaching the ten-packet limit before the probe begins is treated as
a successful bounded observation rather than a startup failure:

```sh
./scripts/diagnose-aws-data-channel run --sudo-tcpdump
```

To distinguish a direct-path failure from an endpoint configuration failure,
an explicitly approved nested diagnostic can preserve exactly one existing
VPN while it temporarily activates `aws-direct`. It verifies that the AWS
endpoint route actually uses that pre-existing tunnel; preserving a tunnel
alone does not prove that the probe is nested. It never manages or changes the
existing VPN and disconnects only `ubuntu-aws-direct`:

```sh
./scripts/diagnose-aws-data-channel run --via-existing-vpn
```

Both explicit checks can be requested in the same run:

```sh
./scripts/diagnose-aws-data-channel run --via-existing-vpn --sudo-tcpdump
```

When direct SSH is unavailable, use the staged workflow instead:

```sh
# Trusted direct IP or operator VPN available for SSH:
./scripts/diagnose-aws-data-channel prepare

# Every other VPN disabled:
./scripts/diagnose-aws-data-channel probe

# Trusted direct IP or operator VPN available for SSH again:
./scripts/diagnose-aws-data-channel finish
```

The intermediate aggregate-only state is mode `0600` under the Git-ignored
`operator-config/` directory. The `probe` phase always attempts to disconnect
`ubuntu-aws-direct` on exit. If a staged run must be abandoned, remove only
that protected local state with `./scripts/diagnose-aws-data-channel reset`.

When the host client emits valid OpenVPN data-channel frames but they do not
reach the AWS pre-conntrack counter, isolate NetworkManager and the host route
configuration with the local container client. This uses the already built
Veilway image without pulling, mounts `ubuntu-aws-direct.ovpn` read-only,
disables Docker logging, keeps all tunnel routes inside a disposable Docker
network namespace, and resolves one public hostname through `10.242.20.1`.
After dropping every capability, the container receives `NET_ADMIN` for its
private TUN interface and `DAC_OVERRIDE` so its root process can read the
operator-owned mode-`0600` bind mount and write into the mode-`0700` temporary
diagnostic directory. The profile mount and container root filesystem remain
read-only.
The explicit `--run` invocation creates and automatically removes only the
named diagnostic container:

```sh
./scripts/diagnose-aws-container-client --run
```

To test the same isolated client through an already active operator VPN, use
the explicit nested mode. It requires exactly one existing host tunnel and
verifies, without printing the endpoint or interface, that the AWS route uses
that tunnel both before container creation and after the Veilway lease is
installed. The script never activates, disconnects, or changes the existing
VPN:

```sh
./scripts/diagnose-aws-container-client --run --via-existing-vpn
```

If the process exits early, a protected temporary log is reduced to the last
reached protocol stage and one non-sensitive failure category. The full log is
never printed, is not stored by Docker, and is deleted together with the exact
diagnostic container during the exit trap. The log lives in a temporary
mode-`0700` directory and is created with a mode-`0600` umask; cleanup removes
the file and then uses `rmdir`, which refuses a non-empty directory. Cleanup is
idempotent when Docker has already removed or stopped the container. The
diagnostic overrides the application image entrypoint with a fixed shell that
uses `exec`, so OpenVPN becomes PID 1 and receives the bounded stop signal
directly.

Run it with every host VPN disabled. It does not replace the Ubuntu acceptance
test; it only distinguishes the NetworkManager-managed client path from an
independent OpenVPN process using the same protected certificate identity.

On a disposable VM, test application rollback by running the same playbook
from the previously accepted repository revision and repeating the health and
connectivity checks. Review a new Terraform plan separately if infrastructure
rollback is required; never reuse an old VM as a rollback target. Destruction
or restoration of cloud resources always requires its own explicit approval.

## 5. Revoke a profile

Use the profile name printed during creation:

```sh
./scripts/veilway-pki profile revoke --name <profile-name>
```

Re-run the explicitly reviewed Ansible playbook for both new endpoints to
upload the shared new CRL. A revoked certificate must be rejected on its next
TLS authentication. Do not delete the CA database entry.

## 6. Acceptance checks

On Ubuntu, first apply the documented NetworkManager persistence and negative
DNS priorities to the imported profile. With every other VPN or system-wide
proxy disabled, run the autonomous test. It uses only the operator-approved
Cloudflare trace endpoint and an `example.com` DNS lookup, never prints the
observed addresses, and disconnects Veilway before returning:

```sh
./scripts/acceptance-ubuntu-direct --mode yc-direct
./scripts/acceptance-ubuntu-direct --mode aws-direct
./scripts/acceptance-ubuntu-direct --mode yc-aws-multihop
```

- On both Ubuntu and iPhone, confirm that DNS uses only the tunnel resolver.
- In `yc-direct`, confirm the public IPv4 is the Yandex address and IPv6 cannot
  connect while the tunnel is active.
- In `aws-direct`, confirm both public IPv4 and public IPv6 belong to AWS.
- In `yc-aws-multihop`, confirm both public addresses belong to AWS and the
  Yandex address is never observed as egress.
- Confirm provider-private CIDRs and `169.254.169.254` are unreachable through
  both profiles.
- Reboot each disposable VM and repeat the connection checks.
- Confirm all expected Compose services report `healthy` after startup and
  reboot.
- In a separately approved failure test, stop only the Yandex transit
  container and confirm the multi-hop profile loses IPv4, IPv6, and DNS while
  `yc-direct` remains usable. Confirm that no traffic falls back to Yandex
  egress, then restore the reviewed Compose stack.
- Revoke one test profile and confirm it cannot reconnect.

Use only operator-approved diagnostic endpoints. Do not publish addresses,
routes, packet captures, Terraform state, or VPN logs.

Run the repository checks before every deployment-affecting change:

```sh
./scripts/check.sh
./scripts/container-smoke.sh
```
