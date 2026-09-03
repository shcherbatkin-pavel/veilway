# Yandex web control plane root

This root creates only the new `veilway.ru` web VM, its dedicated network,
static IPv4, public Cloud DNS zone and apex A record, persistent data disk and service account. The disk attachment has
`auto_delete=false`, so deleting the VM does not delete PostgreSQL data. The service
account receives `compute.operator` through an instance-level binding to the
existing `yc-direct` ID supplied by the operator. It receives no folder role.

Copy `terraform.tfvars.example` to the ignored `terraform.tfvars`, review the
plan, and apply it only as a separately approved operation. After the DNS zone
exists, delegate the domain at REG.RU to the name servers from the
`dns_name_servers` output. Ansible and real VM restarts remain outside this
root.

TCP/22 is reachable from any IPv4 address so the operator is not tied to a
fixed network. SSH access still requires the configured public key; the web
panel separately uses the administrator login and password from the ignored
local `.env`.
