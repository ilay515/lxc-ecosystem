#!/usr/bin/env python3
"""
setup_pve.py - Initial setup script for Proxmox VE VM:
1. Installs local SSH key for passwordless automation
2. Generates the Proxmox API Token (root@pam!lxc-token)
3. Updates the appliance catalog and downloads default Debian 12 template
4. Generates .env and config.yaml
"""

import os
import sys
import subprocess
import re
from pve_utils import get_ssh_client, exec_ssh

KEY_FILE = "id_pve"


def ensure_local_ssh_key():
    """Generates an ed25519 keypair if none exists."""
    if not os.path.exists(KEY_FILE):
        print(f"Generating dedicated SSH keypair ({KEY_FILE})...")
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", KEY_FILE],
            check=True, stdout=subprocess.DEVNULL
        )


def main():
    print("=== Proxmox VE Initial Setup ===")
    ensure_local_ssh_key()

    print("Connecting to Proxmox VE via SSH...")
    client = get_ssh_client()
    print("Connected successfully!")

    # 1. Install SSH public key for passwordless access
    pub_key_path = f"{KEY_FILE}.pub"
    if os.path.exists(pub_key_path):
        with open(pub_key_path) as f:
            pub_key = f.read().strip()
        print("Installing SSH public key on Proxmox host...")
        install_key_cmd = f"mkdir -p /root/.ssh && grep -qF '{pub_key}' /root/.ssh/authorized_keys 2>/dev/null || echo '{pub_key}' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys"
        exec_ssh(client, install_key_cmd)

    # 2. Create API Token
    print("Creating API token 'lxc-token' for root@pam...")
    exec_ssh(client, "pveum user token delete root@pam lxc-token 2>/dev/null || true", check=False)
    out, _, code = exec_ssh(client, "pveum user token add root@pam lxc-token --privsep 0 --output-format json", check=False)

    token_output = out.strip()
    if code != 0 or not token_output:
        out, _, _ = exec_ssh(client, "pveum user token add root@pam lxc-token --privsep 0")
        token_output = out.strip()

    match = re.search(r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})', token_output)
    if not match:
        print(f"Could not parse token secret from: {token_output}", file=sys.stderr)
        sys.exit(1)
    token_value = match.group(1)
    print(f"API Token generated: {token_value}")

    # 3. Update appliance catalog
    print("Updating appliance template catalog (pveam update)...")
    out, _, _ = exec_ssh(client, "pveam update")
    print(out.strip())

    # 4. Find latest Debian 12 template
    print("Checking available Debian 12 template...")
    out, _, _ = exec_ssh(client, "pveam available --section system | grep debian-12 | awk '{print $2}' | sort -V | tail -n 1")
    template_name = out.strip() or "debian-12-standard_12.12-1_amd64.tar.zst"
    print(f"Target template: {template_name}")

    out, _, _ = exec_ssh(client, f"pveam list local | grep {template_name} || true")
    if not out.strip():
        print(f"Downloading template {template_name} to local storage...")
        exec_ssh(client, f"pveam download local {template_name}")
        print("Template download completed!")
    else:
        print(f"Template {template_name} is already available.")

    # 5. Write .env
    env_content = f"""PROXMOX_HOST=127.0.0.1
PROXMOX_PORT=8006
PROXMOX_USER=root@pam
PROXMOX_TOKEN_NAME=lxc-token
PROXMOX_TOKEN_VALUE={token_value}
PROXMOX_VERIFY_SSL=false

PROXMOX_SSH_PORT=2222
PROXMOX_SSH_USER=root
PROXMOX_SSH_KEY=id_pve
"""
    with open(".env", "w") as f:
        f.write(env_content)
    print("Saved active credentials to .env")

    # 6. Write config.yaml if missing
    if not os.path.exists("config.yaml"):
        config_content = f"""proxmox:
  node: "pve"
  storage: "local-lvm"
  template_storage: "local"

container:
  vmid: null
  hostname: "debian12-lxc"
  ostemplate: "{template_name}"
  description: "Debian 12 container managed via lxctest automation"
  cores: 2
  memory: 2048
  swap: 512
  disk_size: "16G"
  unprivileged: true
  nesting: true
  start_after_create: true
  ssh_public_key: ""

network:
  interface: "eth0"
  bridge: "vmbr0"
  ip: "dhcp"
  gateway: ""
  firewall: true
"""
        with open("config.yaml", "w") as f:
            f.write(config_content)
        print("Generated config.yaml")

    client.close()
    print("==========================================================")
    print(" Setup completed successfully!")
    print(" You can now run: python3 create_lxc.py")
    print("==========================================================")


if __name__ == "__main__":
    main()
