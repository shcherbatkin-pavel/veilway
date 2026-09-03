variable "cloud_id" {
  type        = string
  description = "Yandex Cloud ID"
}

variable "folder_id" {
  type        = string
  description = "Dedicated Yandex folder ID"
}

variable "zone" {
  type        = string
  description = "Availability zone for the web VM"
  default     = "ru-central1-a"
}

variable "name_prefix" {
  type        = string
  description = "Resource name prefix"
  default     = "veilway"
}

variable "subnet_cidr" {
  type        = string
  description = "Dedicated web subnet IPv4 CIDR"
}

variable "ssh_public_key" {
  type        = string
  description = "OpenSSH public key for the operator"
  sensitive   = true
}

variable "yc_direct_instance_id" {
  type        = string
  description = "The only Yandex VM that the control service account may restart"
}

variable "platform_id" {
  type        = string
  default     = "standard-v3"
  description = "Yandex Compute platform"
}

variable "data_disk_size_gib" {
  type        = number
  default     = 20
  description = "Persistent PostgreSQL/Caddy data disk size"
}
