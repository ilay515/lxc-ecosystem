#!/usr/bin/env python3
import sys
import paramiko
import re

HOST = "127.0.0.1"
PORT = 2222
USER = "root"
PASSWORD = "12345678"

def main():
    print(f"Connecting to Proxmox VE via SSH ({HOST}:{PORT})...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=10)
    except Exception as e:
        print(f"Failed to connect: {e}")
        sys.exit(1)
        
    print("Connected successfully!")
    
    # 1. Create API Token
    print("Creating API token 'lxc-token' for root@pam...")
    # First delete existing if any
    stdin, stdout, stderr = client.exec_command("pveum user token delete root@pam lxc-token 2>/dev/null || true")
    stdout.channel.recv_exit_status()
    
    stdin, stdout, stderr = client.exec_command("pveum user token add root@pam lxc-token --privsep 0 --output-format json")
    token_output = stdout.read().decode().strip()
    status = stdout.channel.recv_exit_status()
    
    if status != 0:
        # Fallback without json format
        stdin, stdout, stderr = client.exec_command("pveum user token add root@pam lxc-token --privsep 0")
        token_output = stdout.read().decode().strip()
        print("Raw token output:", token_output)
    
    token_value = None
    # Look for UUID or value in output
    match = re.search(r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})', token_output)
    if match:
        token_value = match.group(1)
        print(f"Found Token Secret: {token_value}")
    else:
        print(f"Could not parse token secret from: {token_output}")
        sys.exit(1)
        
    # 2. Update appliance catalog
    print("Updating appliance template catalog (pveam update)...")
    stdin, stdout, stderr = client.exec_command("pveam update")
    print(stdout.read().decode().strip())
    
    # 3. Find Debian 12 template
    print("Finding Debian 12 template...")
    stdin, stdout, stderr = client.exec_command("pveam available --section system | grep debian-12 | awk '{print $2}' | sort -V | tail -n 1")
    template_name = stdout.read().decode().strip()
    if not template_name:
        template_name = "debian-12-standard_12.7-1_amd64.tar.zst"
    print(f"Target template: {template_name}")
    
    # Check if already downloaded
    stdin, stdout, stderr = client.exec_command(f"pveam list local | grep {template_name} || true")
    existing = stdout.read().decode().strip()
    if not existing:
        print(f"Downloading template {template_name} to local storage...")
        stdin, stdout, stderr = client.exec_command(f"pveam download local {template_name}")
        while not stdout.channel.exit_status_ready():
            line = stdout.readline()
            if line:
                print(line.strip())
        print("Template download completed!")
    else:
        print(f"Template {template_name} is already downloaded.")
        
    # 4. Write .env
    env_content = f"""PROXMOX_HOST=127.0.0.1
PROXMOX_PORT=8006
PROXMOX_USER=root@pam
PROXMOX_TOKEN_NAME=lxc-token
PROXMOX_TOKEN_VALUE={token_value}
PROXMOX_VERIFY_SSL=false
"""
    with open("/home/ilay/projects/lxctest/.env", "w") as f:
        f.write(env_content)
    print("Wrote .env with Proxmox API token credentials!")

    # 5. Write config.yaml
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
    with open("/home/ilay/projects/lxctest/config.yaml", "w") as f:
        f.write(config_content)
    print("Wrote config.yaml!")

    client.close()
    print("Setup completed successfully!")

if __name__ == "__main__":
    main()
