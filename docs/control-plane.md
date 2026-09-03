# Restart control plane operator guide

The implementation under `web/` is intentionally limited to restarting
`aws-direct` and `yc-direct`. None of the commands in this guide are run by the
repository or test suite automatically.

## Components and API

The steady state is three containers:

```text
Internet -> Caddy + React -> Unix socket -> FastAPI -> PostgreSQL
                                           |       -> AWS EC2 API
                                           `-------> Yandex Compute API
VPN nodes ------------ outbound heartbeat --------> FastAPI
```

The versioned API provides login, session and logout endpoints, the two-VM
status list, restart job creation/history/detail, and authenticated agent
heartbeats. The schema contains only `Admin`, `AdminSession`, `VpnVm`,
`VmHeartbeat`, `RestartJob`, and `RestartTarget`. Responses never include
instance IDs, IP addresses, heartbeat tokens, credentials or cloud request
IDs.

## 1. Provision in separately approved stages

Review and apply `infra/yandex-web` separately. It creates the new public web
VM, isolated network, TCP/22+80+443 security group, static IPv4, public Cloud
DNS zone with the `veilway.ru` apex A record, service account and persistent
data disk attached with `auto_delete=false`. Its instance-level IAM binding must name only
the existing `yc-direct` ID. Before applying, inspect existing
`compute.operator` access bindings on that VM: the Terraform binding is
authoritative for that role.

Because that role is intentionally scoped to one instance, the backend checks
Yandex restart completion through the instance-specific Compute API operations
list. It must not use the global operation endpoint or broaden the service
account binding to the folder merely to read operation status.

Review and apply `infra/aws-management` separately. It creates an IAM user and
policy but no access key. The planned `RebootInstances` resource must be the
single `aws-direct` ARN and status reads must be limited to one region. Create
the access key manually only after that policy has been reviewed; store it in
the local `.env`, never in Terraform state or command arguments.

After applying the Cloud DNS resources, delegate `veilway.ru` at REG.RU to the
authoritative name servers returned by the Terraform `dns_name_servers`
output. Do not change registrar NS records before the public zone and apex A
record exist. Wait until public resolution is correct before deploying Caddy
so production ACME validation can succeed.

## 2. Prepare protected local inputs

Copy `.env.example` to `.env`, replace every placeholder, and protect it:

```sh
chmod 0600 .env
cp deploy/control-inventory.example.yml deploy/control-inventory.yml
```

Replace the documentation-only addresses in the ignored inventory. The
`control_web` host must be only the new web VM. The `direct_vpn` group must
contain only `aws-direct` and `yc-direct`, with an explicit list of containers
whose health the outbound agent will inspect.

The wrapper rejects unknown `.env` keys, missing values, an unsafe file mode,
or a file not ignored by Git. Without `--apply` it validates locally and does
not contact any host:

```sh
python3 scripts/deploy-web-control.py web
python3 scripts/deploy-web-control.py heartbeat
```

## 3. Deploy only after explicit approval

The following are two distinct mutating operations. Run each only after its
inventory and scope have been approved:

```sh
python3 scripts/deploy-web-control.py web --apply
python3 scripts/deploy-web-control.py heartbeat --apply
```

The web role formats only the Terraform-attached empty `virtio-data` disk,
mounts it at `/srv/veilway-control`, installs three root-owned `0400` runtime
secret files, runs Alembic, synchronizes the administrator and the exact two VM
records through protected stdin, and starts the three containers. It never
copies the complete `.env`.

The PostgreSQL password initializes the database cluster and must remain
unchanged during routine redeployments. Rotating it requires a separate,
explicit database-password procedure; merely editing `.env` intentionally
causes migration to fail rather than silently desynchronizing the database
role and API secret.

The heartbeat role installs only a small read-only Docker inspector and a
15-second systemd timer. It reads boot ID and uptime from `/proc`, reads the
status of the explicitly listed containers, and sends one outbound HTTPS
request. It has no listener or command endpoint and does not change or restart
OpenVPN. To install one node separately, append `--limit aws-direct` or
`--limit yc-direct`.

## 4. Acceptance before a real restart

Before using the restart button, verify all of the following manually:

- `https://veilway.ru` is reachable without either VPN;
- the browser receives a valid certificate and the API/database expose no TCP
  ports;
- both dashboard cards have fresh healthy heartbeats;
- the AWS credentials pass an operator-run `RebootInstances` DryRun for only
  `aws-direct` and cannot target another instance;
- the Yandex service account has no folder role and its instance binding names
  only `yc-direct`;
- the web VM and the legacy OpenVPN Access Server are absent from the managed
  VM table and all IAM target lists.

A real restart is a further explicit action in the UI. For both targets, the
job waits up to 15 minutes for AWS to return with a new healthy boot ID before
dispatching the Yandex restart. `failed` stops the sequence. `needs_review`
means the cloud mutation outcome was ambiguous and the worker deliberately did
not retry it.

## Local verification

The normal test suite uses SQLite plus mocked cloud providers and cannot call
real cloud mutations. Run it in the dedicated test containers; `--build` is an
explicit acknowledgement that Docker may fetch the pinned base images and
package dependencies when they are not cached locally:

```sh
./scripts/test-control-plane.sh --build
```

The runner executes backend pytest without network access or a writable root
filesystem, builds the pinned frontend stage, validates the Compose exposure,
and then runs the disposable PostgreSQL integration check. To run only the
PostgreSQL check (it builds images and creates then removes local containers),
use:

```sh
python3 scripts/control-postgres-smoke.py
```
