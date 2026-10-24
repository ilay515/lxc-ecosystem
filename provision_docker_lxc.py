#!/usr/bin/env python3
"""
provision_docker_lxc.py - Provisions an LXC container and installs Docker & a Docker container inside it.
"""

import time
from create_lxc import load_env, load_config, get_client
from pve_utils import get_ssh_client, exec_ssh, exec_in_lxc

DOCKER_VMID = 101
DOCKER_HOSTNAME = "docker-lxc"


def main():
    env = load_env(".env")
    config = load_config("config.yaml")
    api_client = get_client(env)
    node = config["proxmox"]["node"]
    storage = config["proxmox"]["storage"]
    tmpl_storage = config["proxmox"]["template_storage"]
    ostemplate = config["container"]["ostemplate"]

    print(f"=== 1. Checking if container {DOCKER_VMID} exists ===")
    try:
        existing = api_client.get(f"/nodes/{node}/lxc/{DOCKER_VMID}/status/current")
        print(f"Container {DOCKER_VMID} already exists (status: {existing.get('status')}). Destroying it for a clean build...")
        if existing.get("status") == "running":
            upid = api_client.post(f"/nodes/{node}/lxc/{DOCKER_VMID}/status/stop")
            api_client.wait_for_task(node, upid)
        upid = api_client.delete(f"/nodes/{node}/lxc/{DOCKER_VMID}")
        api_client.wait_for_task(node, upid)
    except Exception:
        print(f"Container {DOCKER_VMID} does not exist yet. Proceeding with creation.")

    print(f"\n=== 2. Creating LXC container {DOCKER_VMID} ({DOCKER_HOSTNAME}) ===")
    payload = {
        "vmid": DOCKER_VMID,
        "hostname": DOCKER_HOSTNAME,
        "ostemplate": f"{tmpl_storage}:vztmpl/{ostemplate}",
        "cores": 2,
        "memory": 2048,
        "swap": 512,
        "rootfs": f"{storage}:16",
        "net0": "name=eth0,bridge=vmbr0,ip=dhcp,firewall=1",
        "unprivileged": 1,
        "features": "nesting=1",
        "start": 0,
        "description": "LXC container running Docker Engine and containers"
    }

    upid = api_client.post(f"/nodes/{node}/lxc", data=payload)
    api_client.wait_for_task(node, upid)
    print(f"LXC Container {DOCKER_VMID} created successfully!")

    print(f"\n=== 3. Connecting to Proxmox VE over SSH ===")
    ssh = get_ssh_client(env)
    print("SSH connected!")

    # Enable keyctl=1, nesting=1, and apparmor unconfined for Docker inside LXC
    print("Configuring LXC for Docker (features: nesting=1,keyctl=1, lxc.apparmor.profile: unconfined)...")
    exec_ssh(ssh, f"pct set {DOCKER_VMID} --features nesting=1,keyctl=1")
    exec_ssh(ssh, f"grep -q 'lxc.apparmor.profile: unconfined' /etc/pve/lxc/{DOCKER_VMID}.conf || echo 'lxc.apparmor.profile: unconfined' >> /etc/pve/lxc/{DOCKER_VMID}.conf")

    # Start the container
    print(f"Starting container {DOCKER_VMID}...")
    exec_ssh(ssh, f"pct start {DOCKER_VMID}")

    # Wait for network and systemd initialization
    print("Waiting 10s for container systemd and network to initialize...")
    time.sleep(10)

    # Check container IP
    out, _, _ = exec_in_lxc(ssh, DOCKER_VMID, "ip -4 addr show eth0 | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}'")
    ct_ip = out.strip()
    print(f"Container IP inside PVE network: {ct_ip}")

    print(f"\n=== 4. Installing Docker inside container {DOCKER_VMID} ===")
    print("Updating APT & installing dependencies (curl, ca-certificates, gnupg)...")
    exec_in_lxc(ssh, DOCKER_VMID, "apt-get update && apt-get install -y ca-certificates curl gnupg lsb-release")

    print("Adding Docker official GPG key...")
    exec_in_lxc(ssh, DOCKER_VMID, "install -m 0755 -d /etc/apt/keyrings")
    exec_in_lxc(ssh, DOCKER_VMID, "curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes")
    exec_in_lxc(ssh, DOCKER_VMID, "chmod a+r /etc/apt/keyrings/docker.gpg")

    print("Configuring Docker APT repository...")
    repo_cmd = 'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list'
    exec_in_lxc(ssh, DOCKER_VMID, repo_cmd)

    print("Installing Docker Engine, containerd, and Docker Compose...")
    exec_in_lxc(ssh, DOCKER_VMID, "apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin")

    print("Enabling and starting Docker service...")
    exec_in_lxc(ssh, DOCKER_VMID, "systemctl enable --now docker")

    out, _, _ = exec_in_lxc(ssh, DOCKER_VMID, "docker --version")
    print(f"Installed: {out.strip()}")

    print(f"\n=== 5. Launching a Docker container inside the LXC container ===")
    print("Running Docker container 'web-nginx' (nginx:alpine on port 80)...")
    exec_in_lxc(ssh, DOCKER_VMID, "docker rm -f web-nginx 2>/dev/null || true")
    exec_in_lxc(ssh, DOCKER_VMID, "docker run -d --name web-nginx -p 80:80 --restart unless-stopped nginx:alpine")

    out, _, _ = exec_in_lxc(ssh, DOCKER_VMID, "docker ps --format 'table {{.ID}}\\t{{.Image}}\\t{{.Status}}\\t{{.Ports}}'")
    print("\nRunning Docker containers inside LXC:")
    print(out)

    print("Testing HTTP response from the Docker container...")
    out, _, _ = exec_in_lxc(ssh, DOCKER_VMID, "curl -sI http://localhost:80 | head -n 5")
    print(out)

    ssh.close()
    print("===================================================================")
    print(f" SUCCESS: Custom container {DOCKER_VMID} ({DOCKER_HOSTNAME}) is running!")
    print(f" Docker Engine and 'nginx:alpine' container are fully operational!")
    print(f" Access container shell with: ssh -p 2222 -t root@localhost \"pct enter {DOCKER_VMID}\"")
    print("===================================================================")


if __name__ == "__main__":
    main()
