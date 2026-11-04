variable "name" {
  description = "Installation name; used for the instance label, host name and firewall group."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.name))
    error_message = "Use 2-31 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "region" {
  description = "Vultr region id (see https://api.vultr.com/v2/regions). sjc = Silicon Valley."
  type        = string
  default     = "sjc"
}

variable "plan" {
  description = "Vultr plan id. vhp-4c-8gb-amd is recommended; vhp-2c-4gb-amd is the minimum."
  type        = string
  default     = "vhp-4c-8gb-amd"
}

variable "os_id" {
  description = "Operating system id. 2284 = Ubuntu 24.04 LTS x64 (from https://api.vultr.com/v2/os)."
  type        = number
  default     = 2284
}

variable "ssh_public_key" {
  description = "Public key for this installation (the installer generates one per install)."
  type        = string

  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp[0-9]+) ", var.ssh_public_key))
    error_message = "Provide a public key line such as 'ssh-ed25519 AAAA... comment'."
  }
}

variable "admin_cidrs" {
  description = "Source networks (IPv4 or IPv6 CIDR) allowed to reach SSH (22). Empty = SSH closed; use the Vultr web console."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.admin_cidrs : can(cidrhost(c, 0))])
    error_message = "Every admin_cidrs entry must be a valid CIDR such as 203.0.113.7/32."
  }
}

variable "enable_backups" {
  description = "Vultr automatic VM backups (extra cost), in addition to the restic backups inside the stack."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Labels attached to the instance."
  type        = list(string)
  default     = ["smb-ops"]
}
