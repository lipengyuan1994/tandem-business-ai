output "ipv4" {
  description = "Public IPv4 address (create the DNS A record for this)."
  value       = vultr_instance.this.main_ip
}

output "ipv6" {
  description = "Public IPv6 address (create the DNS AAAA record for this)."
  value       = vultr_instance.this.v6_main_ip
}

output "instance_id" {
  description = "Vultr instance id."
  value       = vultr_instance.this.id
}

output "firewall_group_id" {
  description = "Firewall group id (the installer removes the SSH rule through it when asked)."
  value       = vultr_firewall_group.this.id
}
