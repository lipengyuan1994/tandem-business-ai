# Demo environment on the hackathon Vultr account (docs/roadmap/10-vultr-infrastructure.md#environments).
# State stays local (terraform.tfstate, gitignored): no remote state backend is authorised.
# Authentication: the API key is read from the TF_VAR_vultr_api_key environment variable (install.sh sets it from
# VULTR_API_KEY). It is never written to a file in this repository.
# Nothing here runs until the owner approves task P4-09: `terraform plan` and `apply` are not part of P4-04.

terraform {
  required_version = ">= 1.6"

  required_providers {
    vultr = {
      source  = "vultr/vultr"
      version = "= 2.33.2"
    }
  }
}

variable "vultr_api_key" {
  description = "Vultr API key. Set through TF_VAR_vultr_api_key only."
  type        = string
  sensitive   = true
}

provider "vultr" {
  api_key     = var.vultr_api_key
  rate_limit  = 100
  retry_limit = 3
}

variable "name" {
  description = "Installation name (install.sh passes it; the demo default is smb-demo)."
  type        = string
  default     = "smb-demo"
}

variable "ssh_public_key" {
  description = "Public key generated for this install."
  type        = string
}

variable "admin_cidrs" {
  description = "Networks allowed to reach SSH; empty keeps SSH closed."
  type        = list(string)
  default     = []
}

variable "region" {
  type    = string
  default = "sjc"
}

variable "plan" {
  type    = string
  default = "vhp-4c-8gb-amd"
}

module "vm" {
  source = "../../modules/vultr-single-vm"

  name           = var.name
  region         = var.region
  plan           = var.plan
  ssh_public_key = var.ssh_public_key
  admin_cidrs    = var.admin_cidrs
  tags           = ["smb-ops", var.name]
}

output "ipv4" { value = module.vm.ipv4 }
output "ipv6" { value = module.vm.ipv6 }
output "instance_id" { value = module.vm.instance_id }
output "firewall_group_id" { value = module.vm.firewall_group_id }
