output "web_public_ipv4" {
  value       = yandex_vpc_address.web.external_ipv4_address[0].address
  description = "Static IPv4 to use for the veilway.ru A record"
}

output "web_service_account_id" {
  value       = yandex_iam_service_account.control.id
  description = "Service account bound only to yc-direct"
}

output "persistent_data_disk_id" {
  value       = yandex_compute_disk.data.id
  description = "Control plane data disk attached with auto_delete=false"
}

output "dns_name_servers" {
  value       = ["ns1.yandexcloud.net.", "ns2.yandexcloud.net."]
  description = "Authoritative name servers to configure at the domain registrar"
}
