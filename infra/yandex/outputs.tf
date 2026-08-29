output "public_ipv4" {
  description = "Static IPv4 endpoint for yc-direct. Treat as infrastructure-sensitive."
  value       = yandex_vpc_address.vpn.external_ipv4_address[0].address
  sensitive   = true
}

output "vpn_ipv4_cidr" {
  value     = var.vpn_ipv4_cidr
  sensitive = true
}

output "multihop_ipv4_cidr" {
  description = "IPv4 client pool routed through the Yandex-to-AWS multi-hop path."
  value       = var.future_multihop_cidr
  sensitive   = true
}

output "transit_ipv4_cidr" {
  description = "IPv4 network used only by the Yandex-to-AWS transit tunnel."
  value       = var.transit_ipv4_cidr
  sensitive   = true
}

output "vpc_cidr" {
  value     = var.vpc_cidr
  sensitive = true
}

output "ansible_host_alias" {
  value = {
    name = "yc-direct"
    user = "ubuntu"
  }
}
