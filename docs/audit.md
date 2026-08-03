# Safe Ubuntu VM audit

The audit script collects read-only networking and service diagnostics from the
host on which it is run. It never connects to a VM, sends data, installs
packages, or changes routes, firewall rules, services, or configuration.

Audit reports are sensitive. They can contain hostnames, public and private IP
addresses, routing tables, listening ports, service state, and firewall rules.
They are not suitable for a public repository, issue, pull request, paste, or
chat message.

## What is collected

- Ubuntu release and kernel information.
- Interface, address, route, policy-routing, and IP-forwarding state.
- Installed OpenVPN, OpenVPN Access Server, and WireGuard package versions.
- Limited systemd state for relevant services, without journals or service
  environment values.
- Listening TCP and UDP sockets without process command lines.
- WireGuard interface presence without keys or peer configuration.
- UFW status and, when explicitly requested, privileged nftables and iptables
  rulesets.

The script does not read VPN configuration directories, keys, certificates,
client profiles, shell history, cloud instance metadata, process command
lines, service environments, or system journals.

## Prepare the script manually

Review `scripts/audit-vm.sh` before using it. Manually place the repository on
each VM, or copy the script while preserving a `scripts/` directory. No remote
copy or login command is included because the operator must choose and verify
the target host.

Run these commands in the repository root on the VM:

```sh
chmod +x scripts/audit-vm.sh
./scripts/audit-vm.sh
```

The first run does not invoke `sudo`. Missing tools and permission-denied
diagnostics are recorded and do not stop the remaining checks.

## Optional privileged firewall audit

If the unprivileged report is insufficient, inspect the warning and command
list emitted by:

```sh
./scripts/audit-vm.sh --sudo
```

This mode may ask for the operator's sudo password. It only uses `sudo` for the
following read-only commands when their underlying utilities are available:

```text
ufw status verbose
nft list ruleset
iptables-save
ip6tables-save
```

The script does not call `sudo -v`; it neither installs tools nor modifies
sudoers. The `--sudo` flag is explicit consent to run the displayed commands.

## Review and handle results

Each run creates `audit-results/<hostname>-<UTC timestamp>/report.txt` in the
repository root. The directory has mode `0700` and the report has mode `0600`.
The whole `audit-results/` tree is ignored by Git.

Before copying a report:

1. Read it on the VM and confirm that it contains only expected diagnostics.
2. Look for unexpected credential material or sensitive firewall comments.
3. Copy only the reviewed result directory to a trusted local machine using a
   transfer method and destination you select manually.
4. Keep the local copy outside version control and do not publish it.
5. After verifying the trusted copy, manually remove VM and local copies that
   are no longer needed according to your retention policy.

Run the procedure separately on the Yandex Cloud and AWS VMs. Compare the
reports privately and share only redacted conclusions in future design work.

