data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "external" "network_validation" {
  program = ["python3", "${path.module}/../../scripts/validate-network-plan.py"]

  query = {
    vpc_cidr             = var.vpc_cidr
    vpn_cidr             = var.vpn_ipv4_cidr
    future_multihop_cidr = var.future_multihop_cidr
    operator_cidrs_json  = jsonencode(var.operator_cidrs.ipv4)
  }
}

resource "random_id" "vpn_ula_global_id" {
  byte_length = 5

  keepers = {
    purpose = "veilway-aws-direct-ula-v1"
  }
}

locals {
  vpn_ula_prefix = "fd${substr(random_id.vpn_ula_global_id.hex, 0, 2)}:${substr(random_id.vpn_ula_global_id.hex, 2, 4)}:${substr(random_id.vpn_ula_global_id.hex, 6, 4)}"
  vpn_ula_cidr   = "${local.vpn_ula_prefix}::/48"
  vpn_ipv6_cidr  = "${local.vpn_ula_prefix}:20::/64"
}

data "external" "ipv6_network_validation" {
  program = ["python3", "${path.module}/../../scripts/validate-network-plan.py"]

  query = {
    vpc_cidr             = aws_vpc.vpn.ipv6_cidr_block
    vpn_cidr             = local.vpn_ipv6_cidr
    future_multihop_cidr = ""
    operator_cidrs_json  = jsonencode(var.operator_cidrs.ipv6)
  }
}

resource "aws_vpc" "vpn" {
  cidr_block                       = var.vpc_cidr
  assign_generated_ipv6_cidr_block = true
  enable_dns_support               = true
  enable_dns_hostnames             = true

  tags = {
    Name = "${var.name_prefix}-aws-direct-vpc"
  }

  lifecycle {
    precondition {
      condition     = data.external.network_validation.result.valid == "true"
      error_message = data.external.network_validation.result.message
    }
  }
}

resource "aws_internet_gateway" "vpn" {
  vpc_id = aws_vpc.vpn.id

  tags = {
    Name = "${var.name_prefix}-aws-direct-igw"
  }
}

resource "aws_subnet" "vpn" {
  vpc_id                          = aws_vpc.vpn.id
  availability_zone               = var.availability_zone
  cidr_block                      = var.vpc_cidr
  ipv6_cidr_block                 = cidrsubnet(aws_vpc.vpn.ipv6_cidr_block, 8, 0)
  assign_ipv6_address_on_creation = true
  map_public_ip_on_launch         = false

  tags = {
    Name = "${var.name_prefix}-aws-direct-subnet"
  }
}

resource "aws_route_table" "vpn" {
  vpc_id = aws_vpc.vpn.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.vpn.id
  }

  route {
    ipv6_cidr_block = "::/0"
    gateway_id      = aws_internet_gateway.vpn.id
  }

  tags = {
    Name = "${var.name_prefix}-aws-direct-routes"
  }
}

resource "aws_route_table_association" "vpn" {
  subnet_id      = aws_subnet.vpn.id
  route_table_id = aws_route_table.vpn.id
}

resource "aws_security_group" "vpn" {
  name        = "${var.name_prefix}-aws-direct"
  description = "Ingress for the dedicated Veilway AWS direct node"
  vpc_id      = aws_vpc.vpn.id

  ingress {
    description      = "SSH from explicitly trusted operator networks"
    protocol         = "tcp"
    from_port        = 22
    to_port          = 22
    cidr_blocks      = var.operator_cidrs.ipv4
    ipv6_cidr_blocks = var.operator_cidrs.ipv6
  }

  ingress {
    description = "OpenVPN Direct MVP over IPv4"
    protocol    = "udp"
    from_port   = 1194
    to_port     = 1194
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "IPv4 path MTU discovery and diagnostics"
    protocol    = "icmp"
    from_port   = -1
    to_port     = -1
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description      = "Required IPv6 neighbor discovery and path MTU"
    protocol         = "icmpv6"
    from_port        = -1
    to_port          = -1
    ipv6_cidr_blocks = ["::/0"]
  }

  egress {
    description      = "Host and explicitly filtered VPN internet egress"
    protocol         = "-1"
    from_port        = 0
    to_port          = 0
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = {
    Name = "${var.name_prefix}-aws-direct-sg"
  }
}

resource "aws_key_pair" "operator" {
  key_name   = "${var.name_prefix}-aws-direct-operator"
  public_key = trimspace(var.ssh_public_key)
}

resource "aws_instance" "vpn" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.vpn.id
  vpc_security_group_ids      = [aws_security_group.vpn.id]
  associate_public_ip_address = false
  ipv6_address_count          = 1
  key_name                    = aws_key_pair.operator.key_name
  source_dest_check           = false

  metadata_options {
    # Ubuntu cloud-init retrieves the selected EC2 public key from IMDS during
    # first boot. Disabling IMDS at launch leaves authorized_keys empty. Keep
    # the minimum viable endpoint: IMDSv2 only, host-only hop limit, no IPv6
    # endpoint, no tags, and no instance IAM profile.
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    http_protocol_ipv6          = "disabled"
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    encrypted   = true
    volume_type = "gp3"
    volume_size = var.root_volume_size_gib
  }

  tags = {
    Name = "${var.name_prefix}-aws-direct"
  }

  lifecycle {
    # The provider reports this launch-time flag as true after the separately
    # managed Elastic IP is attached. Keep false for instance creation, but do
    # not replace the VM solely because the EIP changes the observed value.
    ignore_changes = [ami, associate_public_ip_address]

    precondition {
      condition     = data.external.ipv6_network_validation.result.valid == "true"
      error_message = data.external.ipv6_network_validation.result.message
    }
  }
}

resource "aws_eip" "vpn" {
  domain   = "vpc"
  instance = aws_instance.vpn.id

  tags = {
    Name = "${var.name_prefix}-aws-direct-ipv4"
  }
}
