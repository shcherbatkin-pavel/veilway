data "yandex_compute_image" "ubuntu" {
  family    = "ubuntu-2404-lts"
  folder_id = "standard-images"
}

resource "yandex_vpc_network" "web" {
  name        = "${var.name_prefix}-control-vpc"
  description = "Dedicated network for the public Veilway control plane"
}

resource "yandex_vpc_subnet" "web" {
  name           = "${var.name_prefix}-control-subnet"
  zone           = var.zone
  network_id     = yandex_vpc_network.web.id
  v4_cidr_blocks = [var.subnet_cidr]
}

resource "yandex_vpc_address" "web" {
  name                = "${var.name_prefix}-control-ipv4"
  deletion_protection = true

  external_ipv4_address {
    zone_id = var.zone
  }
}

resource "yandex_vpc_security_group" "web" {
  name       = "${var.name_prefix}-control-web"
  network_id = yandex_vpc_network.web.id

  ingress {
    description    = "HTTP for ACME redirect and challenge"
    protocol       = "TCP"
    port           = 80
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description    = "HTTPS control plane"
    protocol       = "TCP"
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description    = "Public SSH with key authentication"
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description    = "System updates, ACME, cloud APIs and heartbeat responses"
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "yandex_iam_service_account" "control" {
  name        = "${var.name_prefix}-control"
  description = "Restart only the explicitly bound yc-direct VM"
}

resource "yandex_compute_disk" "data" {
  name = "${var.name_prefix}-control-data"
  type = "network-ssd"
  zone = var.zone
  size = var.data_disk_size_gib
}

resource "yandex_compute_instance" "web" {
  name               = "${var.name_prefix}-control-web"
  hostname           = "${var.name_prefix}-control-web"
  zone               = var.zone
  platform_id        = var.platform_id
  service_account_id = yandex_iam_service_account.control.id

  resources {
    cores  = 2
    memory = 4
  }

  boot_disk {
    auto_delete = true

    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = 15
      type     = "network-ssd"
    }
  }

  secondary_disk {
    disk_id     = yandex_compute_disk.data.id
    auto_delete = false
    device_name = "data"
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.web.id
    nat                = true
    nat_ip_address     = yandex_vpc_address.web.external_ipv4_address[0].address
    security_group_ids = [yandex_vpc_security_group.web.id]
  }

  metadata = {
    enable-oslogin     = "false"
    serial-port-enable = "0"
    ssh-keys           = "ubuntu:${trimspace(var.ssh_public_key)}"
  }

  scheduling_policy {
    preemptible = false
  }

  lifecycle {
    ignore_changes = [boot_disk[0].initialize_params[0].image_id]
  }
}

resource "yandex_compute_instance_iam_binding" "restart_yc_direct" {
  instance_id = var.yc_direct_instance_id
  role        = "compute.operator"
  members     = ["serviceAccount:${yandex_iam_service_account.control.id}"]
}
