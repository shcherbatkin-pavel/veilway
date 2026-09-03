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
each VM, or copy only the reviewed script over SSH while preserving a
`scripts/` directory. From the repository root on your trusted local machine,
replace `<vm-user>` and `<vm-host>` and run:

```sh
ssh <vm-user>@<vm-host> 'mkdir -p -- "$HOME/veilway/scripts"'
scp scripts/audit-vm.sh <vm-user>@<vm-host>:veilway/scripts/audit-vm.sh
```

Verify the SSH hostname, account, and host-key fingerprint before accepting a
new connection. Run these commands separately for each VM; do not use wildcards
or copy the local `audit-results/` directory.

Then connect to the selected VM and run these commands in `~/veilway`:

```sh
ssh <vm-user>@<vm-host>
cd ~/veilway
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
3. Note the exact result directory printed by the script. Do not replace it
   with a wildcard or copy the entire remote `audit-results/` directory.

### Copy a reviewed result to the local machine

Run the following commands on your trusted local machine, not on the VM.
Replace `<vm-host>` with the VM's verified SSH hostname or IP address and
`<result-directory>` with the exact directory name printed by the script. If
the VM account is not `ubuntu`, replace that username and its home path too.

```sh
mkdir -p -- ./audit-results
chmod 0700 -- ./audit-results
scp -pr ubuntu@<vm-host>:/home/ubuntu/veilway/audit-results/<result-directory> ./audit-results/
chmod -R go-rwx -- ./audit-results/<result-directory>
```

For example, if the script prints
`/home/ubuntu/veilway/audit-results/<result-directory>/report.txt`, copy the
containing `<result-directory>`, not only an unverified path assembled from the
hostname. Verify the SSH host-key fingerprint before accepting a new
connection.

After copying:

1. Confirm that `./audit-results/<result-directory>/report.txt` exists and is
   readable only by your local user.
2. Keep the local copy in the Git-ignored `audit-results/` directory and do not
   publish it.
3. After verifying the trusted copy, manually remove VM and local copies that
   are no longer needed according to your retention policy.

Run the procedure separately on the Yandex Cloud and AWS VMs. Compare the
reports privately and share only redacted conclusions in future design work.
