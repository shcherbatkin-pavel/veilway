# Veilway contributor instructions

Veilway is a public repository for security-sensitive networking software. Treat
all infrastructure data and generated VPN material as sensitive, even when it
is not a credential by itself.

## Safety boundaries

- Never commit secrets, private keys, certificates, passwords, tokens, client
  profiles, `.env` files, VM audit output, or unredacted infrastructure data.
- Use read-only inspection by default when working with existing hosts or cloud
  resources. Do not connect to a VM automatically.
- Do not create, update, restart, stop, reload, or delete cloud resources,
  routes, firewall rules, services, packages, or system configuration without
  explicit approval for that exact operation.
- The existing OpenVPN Access Server must remain running and unchanged. Do not
  edit its configuration, restart or stop it, remove it, or replace its routes
  and firewall rules.
- Do not add telemetry or send audit data to external services.

## Shell scripts

- Prefer a small, explicit allowlist of commands. Do not use `eval`, remote
  execution, implicit downloads, or dynamically assembled shell commands.
- Quote variable expansions, use `--` where supported, set a restrictive
  `umask` for sensitive output, and handle missing optional commands cleanly.
- Separate privileged commands behind an explicit CLI flag. Print the exact
  privileged operations before invoking `sudo` and keep them read-only.
- Never read VPN configuration directories, private keys, shell history,
  process command lines, cloud instance metadata, service environments, or
  journals as part of an audit.
- Validate scripts with `bash -n` and `shellcheck` when it is already available.
  Do not install tooling merely to run a check.

## Before committing

- Review `git diff` and `git status` for unexpected or generated files.
- Confirm that audit output and local credentials are ignored by Git.
- Search changed files for credential-like values and remove or redact them.
- Document security-impacting behavior and manual operator actions.

