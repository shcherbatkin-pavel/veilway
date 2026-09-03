output "public_ipv4" {
  description = "Static IPv4 endpoint for aws-direct. Treat as infrastructure-sensitive."
  value       = aws_eip.vpn.public_ip
  sensitive   = true
}

output "public_ipv6" {
  description = "Public IPv6 used for aws-direct egress."
  value       = one(aws_instance.vpn.ipv6_addresses)
  sensitive   = true
}

output "vpn_ipv4_cidr" {
  value     = var.vpn_ipv4_cidr
  sensitive = true
}

output "vpn_ipv6_cidr" {
  description = "Persistent generated ULA /64 for aws-direct clients."
  value       = local.vpn_ipv6_cidr
  sensitive   = true
}

output "vpn_ula_cidr" {
  description = "Persistent generated ULA /48 reserved for Veilway address planning."
  value       = local.vpn_ula_cidr
  sensitive   = true
}

output "multihop_ipv4_cidr" {
  description = "IPv4 client pool routed through the Yandex-to-AWS multi-hop path."
  value       = var.future_multihop_cidr
  sensitive   = true
}

output "multihop_ipv6_cidr" {
  description = "Persistent generated ULA /64 for multi-hop clients."
  value       = local.multihop_ipv6_cidr
  sensitive   = true
}

output "transit_ipv4_cidr" {
  description = "IPv4 network used only by the Yandex-to-AWS transit tunnel."
  value       = var.transit_ipv4_cidr
  sensitive   = true
}

output "transit_ipv6_cidr" {
  description = "Persistent generated ULA /64 used only by the Yandex-to-AWS transit tunnel."
  value       = local.transit_ipv6_cidr
  sensitive   = true
}

output "vpc_cidr" {
  value     = var.vpc_cidr
  sensitive = true
}

output "vpc_ipv6_cidr" {
  description = "AWS VPC IPv6 range that VPN forwarding must explicitly deny."
  value       = aws_vpc.vpn.ipv6_cidr_block
  sensitive   = true
}

output "ansible_host_alias" {
  value = {
    name = "aws-direct"
    user = "ubuntu"
  }
}
