#!/usr/bin/env python3
"""
create_docker_template.py - Creates a custom Proxmox LXC appliance template (.tar.zst)
from an existing container with Docker and pre-pulled images included.
"""

import argparse
import time
from pve_utils import get_ssh_client, exec_ssh


def create_template(source_vmid, template_name, images, template_dir="/var/lib/vz/template/cache"):
    print(f"Connecting to Proxmox VE via SSH...")
    ssh = get_ssh_client()
    print("SSH connected!")

    # 1. Start source container if stopped
    out, _, _ = exec_ssh(ssh, f"pct status {source_vmid}", check=False)
    if "running" not in out:
        print(f"Starting source container {source_vmid}...")
        exec_ssh(ssh, f"pct start {source_vmid}")
        time.sleep(5)

    # 2. Inside container: pull images and clean system
    image_pull_cmds = "\n".join([f"    docker pull {img}" for img in images])
    print(f"Preparing container {source_vmid}: pulling Docker images ({', '.join(images)}) and cleaning system...")
    prep_script = f"""
    systemctl start docker
{image_pull_cmds}
    docker rm -f $(docker ps -aq) 2>/dev/null || true
    systemctl stop docker
    systemctl enable docker
    apt-get clean
    rm -rf /var/lib/apt/lists/*
    truncate -s 0 /etc/machine-id
    rm -f /etc/resolv.conf
    rm -f /root/.bash_history
    """
    exec_ssh(ssh, f"pct exec {source_vmid} -- bash -c '{prep_script}'")

    # 3. Create the template tarball: stream tar from container into zstd on PVE host
    dest_tar = f"{template_dir}/{template_name}"
    print(f"Streaming rootfs archive into {dest_tar}...")
    tar_pipe_cmd = (
        f"pct exec {source_vmid} -- tar --numeric-owner "
        f"--exclude='./proc/*' --exclude='./sys/*' --exclude='./dev/*' "
        f"--exclude='./tmp/*' --exclude='./run/*' -cf - -C / . "
        f"| zstd -T0 -1 -o {dest_tar} -f"
    )
    exec_ssh(ssh, tar_pipe_cmd)
    exec_ssh(ssh, f"chmod 644 {dest_tar}")

    # 4. Check template list
    print("\nProxmox available templates:")
    out, _, _ = exec_ssh(ssh, "pveam list local")
    print(out)

    ssh.close()
    print("===================================================================")
    print(f" SUCCESS: Custom template created: {template_name}")
    print(f" Available in Proxmox storage as: local:vztmpl/{template_name}")
    print("===================================================================")


def main():
    parser = argparse.ArgumentParser(description="Create custom Proxmox LXC appliance template (.tar.zst) with Docker")
    parser.add_argument("--vmid", type=int, default=101, help="Source container VMID (default: 101)")
    parser.add_argument("--name", default="debian-12-docker.tar.zst", help="Template filename (default: debian-12-docker.tar.zst)")
    parser.add_argument("--images", nargs="+", default=["nginx:alpine", "alpine:latest"], help="Docker images to pre-cache")
    args = parser.parse_args()

    create_template(args.vmid, args.name, args.images)


if __name__ == "__main__":
    main()
