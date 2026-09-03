variable "aws_account_id" {
  description = "Expected 12-digit AWS account ID; prevents applying to another credential context."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit AWS account ID."
  }
}

variable "aws_region" {
  description = "AWS region in which to create the dedicated VPN VM."
  type        = string
  nullable    = false

  validation {
    condition     = length(trimspace(var.aws_region)) > 0
    error_message = "aws_region must not be empty."
  }
}

variable "availability_zone" {
  description = "Availability zone for the VPN subnet."
  type        = string
  nullable    = false

  validation {
    condition     = length(trimspace(var.availability_zone)) > 0
    error_message = "availability_zone must not be empty."
  }
}

variable "vpc_cidr" {
  description = "Dedicated AWS VPC IPv4 CIDR."
  type        = string
  default     = "10.241.2.0/24"
}

variable "vpn_ipv4_cidr" {
  description = "OpenVPN IPv4 client pool, used for overlap validation and Ansible output."
  type        = string
  default     = "10.242.20.0/24"
}

variable "future_multihop_cidr" {
  description = "IPv4 client pool routed through the Yandex-to-AWS multi-hop path."
  type        = string
  default     = "10.242.30.0/24"
}

variable "transit_ipv4_cidr" {
  description = "Point-to-point IPv4 network for the Yandex-to-AWS OpenVPN transit."
  type        = string
  default     = "10.242.40.0/29"
}

variable "enable_multihop" {
  description = "Explicitly open the restricted AWS transit ingress for the accepted multi-hop phase."
  type        = bool
  default     = false
}

variable "yc_transit_source_cidr" {
  description = "Static public IPv4 of the Yandex node as a canonical /32; the AWS transit listener accepts only this source."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = !var.enable_multihop || (
      can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/32$", var.yc_transit_source_cidr)) &&
      can(cidrhost(var.yc_transit_source_cidr, 0))
    )
    error_message = "yc_transit_source_cidr must be a valid IPv4 host /32 when enable_multihop is true."
  }
}

variable "operator_cidrs" {
  description = "Trusted source CIDRs from which SSH is allowed. At least one CIDR is required."
  type = object({
    ipv4 = list(string)
    ipv6 = optional(list(string), [])
  })

  validation {
    condition     = length(var.operator_cidrs.ipv4) + length(var.operator_cidrs.ipv6) > 0
    error_message = "operator_cidrs must contain at least one IPv4 or IPv6 CIDR."
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

variable "instance_type" {
  description = "EC2 instance type for the single-user Direct MVP."
  type        = string
  default     = "t3.small"
}

variable "root_volume_size_gib" {
  description = "Encrypted gp3 root volume size."
  type        = number
  default     = 20

  validation {
    condition     = var.root_volume_size_gib >= 16
    error_message = "root_volume_size_gib must be at least 16 GiB."
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
