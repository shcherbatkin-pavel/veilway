# Direct VPN MVP deployment

This guide describes an explicit operator workflow for new, dedicated VMs. It
does not authorize connecting to or changing any existing host. Review every
Terraform plan and the Ansible target inventory before continuing.

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

## 3. Prepare PKI and local configuration

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
```

Validate the real profiles without printing their keys, certificates,
fingerprints, or endpoints:

```sh
./scripts/verify-client-profiles
```

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
```

## 4. Configure the new VMs

Install the required Ansible collection locally:

```sh
ansible-galaxy collection install -r deploy/requirements.yml
```

Generate the ignored `deploy/inventory.yml` from the protected Terraform state,
endpoint file, and independently verified host-key files. The generator refuses
to overwrite an existing inventory and does not print addresses or CIDRs:

```sh
./scripts/render-inventory.py
```

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

The managed firewall keeps two named diagnostic counters for UDP/1194.
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
```

- On both Ubuntu and iPhone, confirm that DNS uses only the tunnel resolver.
- In `yc-direct`, confirm the public IPv4 is the Yandex address and IPv6 cannot
  connect while the tunnel is active.
- In `aws-direct`, confirm both public IPv4 and public IPv6 belong to AWS.
- Confirm provider-private CIDRs and `169.254.169.254` are unreachable through
  both profiles.
- Reboot each disposable VM and repeat the connection checks.
- Confirm both Compose services report `healthy` after startup and reboot.
- Revoke one test profile and confirm it cannot reconnect.

Use only operator-approved diagnostic endpoints. Do not publish addresses,
routes, packet captures, Terraform state, or VPN logs.

Run the repository checks before every deployment-affecting change:

```sh
./scripts/check.sh
./scripts/container-smoke.sh
```
