data "yandex_compute_image" "ubuntu" {
  family    = "ubuntu-2404-lts"
  folder_id = "standard-images"
}

data "external" "network_validation" {
  program = ["python3", "${path.module}/../../scripts/validate-network-plan.py"]

  query = {
    vpc_cidr             = var.vpc_cidr
    vpn_cidr             = var.vpn_ipv4_cidr
    future_multihop_cidr = var.future_multihop_cidr
    transit_cidr         = var.transit_ipv4_cidr
    operator_cidrs_json  = jsonencode(var.operator_cidrs)
  }
}

resource "yandex_vpc_network" "vpn" {
  name        = "${var.name_prefix}-yc-direct-vpc"
  description = "Dedicated network for Veilway yc-direct"

  labels = {
    project    = "veilway"
    component  = "yc-direct"
    managed-by = "terraform"
  }

  lifecycle {
    precondition {
      condition     = data.external.network_validation.result.valid == "true"
      error_message = data.external.network_validation.result.message
    }
  }
}

resource "yandex_vpc_subnet" "vpn" {
  name           = "${var.name_prefix}-yc-direct-subnet"
  description    = "Dedicated IPv4 subnet for Veilway yc-direct"
  zone           = var.zone
  network_id     = yandex_vpc_network.vpn.id
  v4_cidr_blocks = [var.vpc_cidr]
}

resource "yandex_vpc_address" "vpn" {
  name                = "${var.name_prefix}-yc-direct-ipv4"
  deletion_protection = false

  external_ipv4_address {
    zone_id = var.zone
  }
}

resource "yandex_vpc_security_group" "vpn" {
  name        = "${var.name_prefix}-yc-direct"
  description = "Ingress for the dedicated Veilway Yandex direct node"
  network_id  = yandex_vpc_network.vpn.id

  ingress {
    description    = "SSH from explicitly trusted operator networks"
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = var.operator_cidrs
  }

  ingress {
    description    = "OpenVPN Direct MVP"
    protocol       = "UDP"
    port           = 1194
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  dynamic "ingress" {
    for_each = var.enable_multihop ? [true] : []

    content {
      description    = "OpenVPN multi-hop ingress"
      protocol       = "UDP"
      port           = 1195
      v4_cidr_blocks = ["0.0.0.0/0"]
    }
  }

  ingress {
    description    = "IPv4 path MTU discovery and diagnostics"
    protocol       = "ICMP"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description    = "Host and explicitly filtered VPN internet egress"
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "yandex_compute_instance" "vpn" {
  name        = "${var.name_prefix}-yc-direct"
  hostname    = "${var.name_prefix}-yc-direct"
  description = "Dedicated Veilway yc-direct VPN node"
  zone        = var.zone
  platform_id = var.platform_id

  resources {
    cores  = var.cores
    memory = var.memory_gib
  }

  boot_disk {
    auto_delete = true

    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = var.root_disk_size_gib
      type     = "network-ssd"
    }
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.vpn.id
    nat                = true
    nat_ip_address     = yandex_vpc_address.vpn.external_ipv4_address[0].address
    security_group_ids = [yandex_vpc_security_group.vpn.id]
  }

  metadata = {
    enable-oslogin     = "false"
    serial-port-enable = "0"
    ssh-keys           = "ubuntu:${trimspace(var.ssh_public_key)}"
  }

  scheduling_policy {
    preemptible = false
  }

  labels = {
    project    = "veilway"
    component  = "yc-direct"
    managed-by = "terraform"
  }

  lifecycle {
    ignore_changes = [boot_disk[0].initialize_params[0].image_id]
  }
}
