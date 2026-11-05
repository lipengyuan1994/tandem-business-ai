# One VM running Docker Compose behind a Vultr firewall group (docs/roadmap/10-vultr-infrastructure.md).
# Inbound: 80 and 443 from anywhere (IPv4 and IPv6); 22 only from var.admin_cidrs. Egress is controlled on the host.

locals {
  anywhere = {
    v4 = { subnet = "0.0.0.0", size = 0 }
    v6 = { subnet = "::", size = 0 }
  }

  public_rules = {
    for pair in setproduct(["80", "443"], ["v4", "v6"]) :
    "${pair[0]}-${pair[1]}" => { port = pair[0], ip_type = pair[1] }
  }

  ssh_rules = {
    for cidr in var.admin_cidrs : cidr => {
      ip_type = strcontains(cidr, ":") ? "v6" : "v4"
      subnet  = split("/", cidr)[0]
      size    = tonumber(split("/", cidr)[1])
    }
  }
}

resource "vultr_ssh_key" "this" {
  name    = "${var.name}-install"
  ssh_key = var.ssh_public_key
}

resource "vultr_firewall_group" "this" {
  description = "${var.name}: 80/443 public, 22 from admin_cidrs"
}

resource "vultr_firewall_rule" "public" {
  for_each          = local.public_rules
  firewall_group_id = vultr_firewall_group.this.id
  protocol          = "tcp"
  ip_type           = each.value.ip_type
  subnet            = local.anywhere[each.value.ip_type].subnet
  subnet_size       = local.anywhere[each.value.ip_type].size
  port              = each.value.port
  notes             = "public web (${each.value.port})"
}

resource "vultr_firewall_rule" "ssh" {
  for_each          = local.ssh_rules
  firewall_group_id = vultr_firewall_group.this.id
  protocol          = "tcp"
  ip_type           = each.value.ip_type
  subnet            = each.value.subnet
  subnet_size       = each.value.size
  port              = "22"
  notes             = "ssh from ${each.key}"
}

resource "vultr_instance" "this" {
  label             = var.name
  hostname          = var.name
  region            = var.region
  plan              = var.plan
  os_id             = var.os_id
  enable_ipv6       = true
  backups           = var.enable_backups ? "enabled" : "disabled"
  activation_email  = false
  ssh_key_ids       = [vultr_ssh_key.this.id]
  firewall_group_id = vultr_firewall_group.this.id
  tags              = var.tags

  # The provider base64-encodes user_data itself. The template contains no secrets (see user-data.yaml.tftpl).
  user_data = templatefile("${path.module}/user-data.yaml.tftpl", {
    ssh_public_key = trimspace(var.ssh_public_key)
    admin_cidrs    = var.admin_cidrs
  })
}
