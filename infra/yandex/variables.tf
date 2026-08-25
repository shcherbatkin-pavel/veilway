variable "cloud_id" {
  description = "Yandex Cloud identifier."
  type        = string
  nullable    = false

  validation {
    condition     = length(trimspace(var.cloud_id)) > 0
    error_message = "cloud_id must not be empty."
  }
}

variable "folder_id" {
  description = "Yandex Cloud folder identifier."
  type        = string
  nullable    = false

  validation {
    condition     = length(trimspace(var.folder_id)) > 0
    error_message = "folder_id must not be empty."
  }
}

variable "zone" {
  description = "Availability zone for the dedicated VPN VM."
  type        = string
  nullable    = false

  validation {
    condition     = length(trimspace(var.zone)) > 0
    error_message = "zone must not be empty."
  }
}

variable "vpc_cidr" {
  description = "Dedicated Yandex VPC IPv4 CIDR."
  type        = string
  default     = "10.241.1.0/24"
}

variable "vpn_ipv4_cidr" {
  description = "OpenVPN IPv4 client pool, used for overlap validation and Ansible output."
  type        = string
  default     = "10.242.10.0/24"
}

variable "future_multihop_cidr" {
  description = "Reserved future multi-hop pool; no route or port is created for it."
  type        = string
  default     = "10.242.30.0/24"
}

variable "operator_cidrs" {
  description = "Trusted IPv4 source CIDRs from which SSH is allowed."
  type        = list(string)

  validation {
    condition     = length(var.operator_cidrs) > 0
    error_message = "operator_cidrs must contain at least one IPv4 CIDR."
  }
}

variable "ssh_public_key" {
  description = "OpenSSH public key installed for the ubuntu operator account."
  type        = string
  nullable    = false
  sensitive   = true

  validation {
    condition     = can(regex("^(ssh-ed25519|ecdsa-sha2-nistp256|sk-ssh-ed25519@openssh.com) [A-Za-z0-9+/]+={0,3}( [^\\r\\n]+)?$", trimspace(var.ssh_public_key)))
    error_message = "Use exactly one Ed25519, security-key Ed25519, or ECDSA P-256 public SSH key."
  }
}

variable "platform_id" {
  description = "Yandex Compute Cloud hardware platform."
  type        = string
  default     = "standard-v3"
}

variable "cores" {
  description = "vCPU count."
  type        = number
  default     = 2
}

variable "memory_gib" {
  description = "RAM size in GiB."
  type        = number
  default     = 2
}

variable "root_disk_size_gib" {
  description = "Root disk size."
  type        = number
  default     = 20

  validation {
    condition     = var.root_disk_size_gib >= 16
    error_message = "root_disk_size_gib must be at least 16 GiB."
  }
}

variable "name_prefix" {
  description = "Prefix used for resource names."
  type        = string
  default     = "veilway"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name_prefix))
    error_message = "name_prefix must be 3-31 lowercase letters, digits, or hyphens and start with a letter."
  }
}
