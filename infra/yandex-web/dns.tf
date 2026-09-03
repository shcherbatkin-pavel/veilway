resource "yandex_dns_zone" "veilway" {
  name        = "${var.name_prefix}-public-zone"
  description = "Public authoritative zone for the Veilway control plane"
  zone        = "veilway.ru."
  public      = true
}

resource "yandex_dns_recordset" "apex_a" {
  zone_id = yandex_dns_zone.veilway.id
  name    = "veilway.ru."
  type    = "A"
  ttl     = 300
  data    = [yandex_vpc_address.web.external_ipv4_address[0].address]
}
