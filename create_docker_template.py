#!/usr/bin/env python3
"""
create_docker_template.py - Creates a custom Proxmox LXC appliance template (.tar.zst)
from container 101 with Docker and pre-pulled images included.
"""

import sys
import time
import paramiko

SSH_HOST = "127.0.0.1"
SSH_PORT = 2222
SSH_USER = "root"
SSH_PASS = "12345678"

SOURCE_VMID = 101
TEMPLATE_NAME = "debian-12-docker.tar.zst"
TEMPLATE_DIR = "/var/lib/vz/template/cache"

def exec_ssh(client, cmd, check=True):
    print(f">> [PVE Host] {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode()
    err = stderr.read().decode()
    exit_code = stdout.channel.recv_exit_status()
    if check and exit_code != 0:
        print(f"Error (exit {exit_code}):\nSTDOUT: {out}\nSTDERR: {err}", file=sys.stderr)
        raise RuntimeError(f"Command failed: {cmd}")
    return out, err, exit_code

def main():
    print(f"Connecting to Proxmox VE via SSH ({SSH_HOST}:{SSH_PORT})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SSH_HOST, port=SSH_PORT, username=SSH_USER, password=SSH_PASS, timeout=10)
    print("SSH connected!")

    # 1. Start source container if stopped
    out, _, _ = exec_ssh(ssh, f"pct status {SOURCE_VMID}", check=False)
    if "running" not in out:
        print(f"Starting source container {SOURCE_VMID}...")
        exec_ssh(ssh, f"pct start {SOURCE_VMID}")
        time.sleep(5)

    # 2. Inside container: pull images and ensure docker service is enabled
    print("Preparing container: pulling Docker images and cleaning system...")
    prep_script = """
    systemctl start docker
    docker pull nginx:alpine
    docker pull alpine:latest
    docker rm -f $(docker ps -aq) 2>/dev/null || true
    systemctl stop docker
    systemctl enable docker
    apt-get clean
    rm -rf /var/lib/apt/lists/*
    truncate -s 0 /etc/machine-id
    rm -f /etc/resolv.conf
    rm -f /root/.bash_history
    """
    exec_ssh(ssh, f"pct exec {SOURCE_VMID} -- bash -c '{prep_script}'")

    # 3. Create the template tarball: stream tar from container into zstd on PVE host
    dest_tar = f"{TEMPLATE_DIR}/{TEMPLATE_NAME}"
    print(f"Streaming rootfs archive into {dest_tar}...")
    tar_pipe_cmd = f"pct exec {SOURCE_VMID} -- tar --numeric-owner --exclude='./proc/*' --exclude='./sys/*' --exclude='./dev/*' --exclude='./tmp/*' --exclude='./run/*' -cf - -C / . | zstd -T0 -1 -o {dest_tar} -f"
    exec_ssh(ssh, tar_pipe_cmd)
    exec_ssh(ssh, f"chmod 644 {dest_tar}")

    # 4. Check template list
    print("\nProxmox available templates:")
    out, _, _ = exec_ssh(ssh, "pveam list local")
    print(out)

    ssh.close()
    print("===================================================================")
    print(f" SUCCESS: Custom template created: {TEMPLATE_NAME}")
    print(f" Available in Proxmox storage as: local:vztmpl/{TEMPLATE_NAME}")
    print("===================================================================")

if __name__ == "__main__":
    main()
