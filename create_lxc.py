#!/usr/bin/env python3
"""
create_lxc.py - Proxmox VE REST API LXC Container Provisioning and Management
"""

import os
import sys
import time
import argparse
import subprocess
import yaml
import requests
import urllib3

# Suppress insecure SSL warnings when verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ProxmoxClient:
    def __init__(self, host, port, user, token_name, token_value, verify_ssl=False):
        self.base_url = f"https://{host}:{port}/api2/json"
        self.verify = verify_ssl
        self.headers = {
            "Authorization": f"PVEAPIToken={user}!{token_name}={token_value}",
            "Accept": "application/json"
        }

    def _url(self, path):
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def get(self, path, params=None):
        resp = requests.get(self._url(path), headers=self.headers, params=params, verify=self.verify, timeout=30)
        resp.raise_for_status()
        return resp.json().get("data")

    def post(self, path, data=None):
        resp = requests.post(self._url(path), headers=self.headers, data=data, verify=self.verify, timeout=60)
        resp.raise_for_status()
        return resp.json().get("data")

    def delete(self, path, params=None):
        resp = requests.delete(self._url(path), headers=self.headers, params=params, verify=self.verify, timeout=60)
        resp.raise_for_status()
        return resp.json().get("data")

    def wait_for_task(self, node, upid, timeout=300, poll_interval=2):
        print(f"Waiting for task UPID {upid}...")
        start_time = time.time()
        while time.time() - start_time < timeout:
            data = self.get(f"/nodes/{node}/tasks/{upid}/status")
            status = data.get("status")
            if status == "stopped":
                exitstatus = data.get("exitstatus")
                if exitstatus == "OK":
                    print(f"Task completed successfully: {exitstatus}")
                    return True
                else:
                    raise RuntimeError(f"Task finished with error: {exitstatus}")
            time.sleep(poll_interval)
        raise TimeoutError(f"Task {upid} did not complete within {timeout} seconds.")


def load_env(env_path=".env"):
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()
    return env_vars


def load_config(config_path="config.yaml"):
    if not os.path.exists(config_path):
        # Fallback to config.example.yaml
        config_path = "config.example.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_client(env):
    host = os.getenv("PROXMOX_HOST", env.get("PROXMOX_HOST", "127.0.0.1"))
    port = int(os.getenv("PROXMOX_PORT", env.get("PROXMOX_PORT", 8006)))
    user = os.getenv("PROXMOX_USER", env.get("PROXMOX_USER", "root@pam"))
    token_name = os.getenv("PROXMOX_TOKEN_NAME", env.get("PROXMOX_TOKEN_NAME", "lxc-token"))
    token_value = os.getenv("PROXMOX_TOKEN_VALUE", env.get("PROXMOX_TOKEN_VALUE", ""))
    verify_ssl = os.getenv("PROXMOX_VERIFY_SSL", env.get("PROXMOX_VERIFY_SSL", "false")).lower() in ("true", "1")

    if not token_value or token_value.startswith("00000000"):
        print("ERROR: PROXMOX_TOKEN_VALUE is not set or has placeholder value in .env", file=sys.stderr)
        sys.exit(1)

    return ProxmoxClient(host, port, user, token_name, token_value, verify_ssl)


def preflight_check(client, config):
    print("--- [Pre-flight Checks] ---")
    # 1. Check version
    ver_data = client.get("/version")
    print(f"Connected to Proxmox VE Version: {ver_data.get('version')} (Release: {ver_data.get('release')})")

    # 2. Check node
    node = config["proxmox"]["node"]
    nodes = client.get("/nodes")
    node_names = [n["node"] for n in nodes]
    if node not in node_names:
        raise ValueError(f"Configured node '{node}' not found in cluster. Available nodes: {node_names}")
    print(f"Target node '{node}' verified (online).")

    # 3. Check storage
    storage_name = config["proxmox"]["storage"]
    storages = client.get(f"/nodes/{node}/storage")
    storage_names = [s["storage"] for s in storages]
    if storage_name not in storage_names:
        # Check if local or another storage exists
        print(f"Warning: storage '{storage_name}' not listed in {storage_names}. Checking if available...")
    else:
        print(f"Target storage '{storage_name}' verified.")

    # 4. Check template
    tmpl_storage = config["proxmox"]["template_storage"]
    target_tmpl = config["container"]["ostemplate"]
    contents = client.get(f"/nodes/{node}/storage/{tmpl_storage}/content", params={"content": "vztmpl"})
    available_templates = [os.path.basename(item["volid"]) for item in contents]
    
    matched_template = None
    for tmpl in available_templates:
        if tmpl == target_tmpl or (target_tmpl in tmpl) or ("debian-12" in tmpl and "debian-12" in target_tmpl):
            matched_template = tmpl
            break
            
    if not matched_template:
        print(f"Warning: Template '{target_tmpl}' not found in '{tmpl_storage}'. Available: {available_templates}")
    else:
        print(f"Container template found: {matched_template} (in {tmpl_storage})")
        config["container"]["ostemplate"] = matched_template

    print("Pre-flight checks passed!\n")
    return True


def create_container(client, config, dry_run=False):
    node = config["proxmox"]["node"]
    storage = config["proxmox"]["storage"]
    tmpl_storage = config["proxmox"]["template_storage"]
    ct_cfg = config["container"]
    net_cfg = config["network"]

    # VMID
    vmid = ct_cfg.get("vmid")
    if not vmid:
        vmid = int(client.get("/cluster/nextid"))
        print(f"Auto-allocated next VMID: {vmid}")

    # Net configuration string
    net_str = f"name={net_cfg['interface']},bridge={net_cfg['bridge']}"
    if net_cfg.get("ip"):
        net_str += f",ip={net_cfg['ip']}"
    if net_cfg.get("gateway"):
        net_str += f",gw={net_cfg['gateway']}"
    if net_cfg.get("firewall"):
        net_str += ",firewall=1"

    # Rootfs configuration string
    disk_size = ct_cfg.get("disk_size", "16G").replace("G", "")
    rootfs_str = f"{storage}:{disk_size}"

    start_requested = ct_cfg.get("start_after_create", True)

    payload = {
        "vmid": vmid,
        "hostname": ct_cfg.get("hostname", f"lxc-{vmid}"),
        "ostemplate": f"{tmpl_storage}:vztmpl/{ct_cfg['ostemplate']}",
        "cores": ct_cfg.get("cores", 2),
        "memory": ct_cfg.get("memory", 2048),
        "swap": ct_cfg.get("swap", 512),
        "rootfs": rootfs_str,
        "net0": net_str,
        "unprivileged": 1 if ct_cfg.get("unprivileged", True) else 0,
        "start": 0,
    }

    if ct_cfg.get("description"):
        payload["description"] = ct_cfg["description"]

    if ct_cfg.get("nesting", True):
        payload["features"] = "nesting=1"

    # SSH key
    ssh_key_path = ct_cfg.get("ssh_public_key")
    if ssh_key_path and os.path.exists(os.path.expanduser(ssh_key_path)):
        with open(os.path.expanduser(ssh_key_path)) as f:
            payload["ssh-public-keys"] = f.read().strip()

    print(f"Creating LXC container {vmid} ('{payload['hostname']}') on node '{node}'...")
    print(f"  Specs: {payload['cores']} cores, {payload['memory']}MB RAM, {payload['rootfs']} rootfs")
    print(f"  Template: {payload['ostemplate']}")
    print(f"  Network: {payload['net0']}")

    if dry_run:
        print("\n[DRY RUN] Request payload verified. No changes made.")
        return vmid

    upid = client.post(f"/nodes/{node}/lxc", data=payload)
    client.wait_for_task(node, upid)
    print(f"LXC Container {vmid} created successfully!")

    # Auto-configure Docker support (AppArmor unconfined + keyctl) if SSH key is available
    ssh_key = os.path.abspath("id_pve")
    if os.path.exists(ssh_key):
        ssh_cmd = f"grep -q 'lxc.apparmor.profile: unconfined' /etc/pve/lxc/{vmid}.conf || echo 'lxc.apparmor.profile: unconfined' >> /etc/pve/lxc/{vmid}.conf; pct set {vmid} --features nesting=1,keyctl=1"
        try:
            subprocess.run(
                ["ssh", "-i", ssh_key, "-p", "2222", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", "root@127.0.0.1", ssh_cmd],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            print("Configured Docker support: nesting=1, keyctl=1, lxc.apparmor.profile: unconfined")
        except Exception:
            pass

    if start_requested:
        print(f"Starting LXC container {vmid}...")
        start_upid = client.post(f"/nodes/{node}/lxc/{vmid}/status/start")
        client.wait_for_task(node, start_upid)

    # Verify status
    status_data = client.get(f"/nodes/{node}/lxc/{vmid}/status/current")
    print(f"Container Status: {status_data.get('status')}")

    # Discover network IP
    print("Querying container interfaces (waiting 5s for DHCP)...")
    time.sleep(5)
    try:
        ifaces = client.get(f"/nodes/{node}/lxc/{vmid}/interfaces")
        print("Assigned Network Interfaces:")
        for iface in ifaces:
            print(f"  {iface.get('name')}: inet={iface.get('inet')}, hwaddr={iface.get('hwaddr')}")
    except Exception as e:
        print(f"Could not query guest interfaces: {e}")

    return vmid


def get_status(client, node, vmid):
    status_data = client.get(f"/nodes/{node}/lxc/{vmid}/status/current")
    print(f"LXC Container {vmid} Status:")
    for k in ["status", "name", "cpus", "maxmem", "uptime", "vmid"]:
        if k in status_data:
            print(f"  {k}: {status_data[k]}")
    try:
        ifaces = client.get(f"/nodes/{node}/lxc/{vmid}/interfaces")
        print("Interfaces:")
        for iface in ifaces:
            print(f"  {iface.get('name')}: inet={iface.get('inet')}")
    except Exception:
        pass


def start_container(client, node, vmid):
    print(f"Starting LXC container {vmid}...")
    upid = client.post(f"/nodes/{node}/lxc/{vmid}/status/start")
    client.wait_for_task(node, upid)
    print(f"Container {vmid} started.")


def stop_container(client, node, vmid):
    print(f"Stopping LXC container {vmid}...")
    upid = client.post(f"/nodes/{node}/lxc/{vmid}/status/stop")
    client.wait_for_task(node, upid)
    print(f"Container {vmid} stopped.")


def destroy_container(client, node, vmid):
    print(f"Destroying LXC container {vmid}...")
    upid = client.delete(f"/nodes/{node}/lxc/{vmid}")
    client.wait_for_task(node, upid)
    print(f"Container {vmid} destroyed.")


def main():
    parser = argparse.ArgumentParser(description="Proxmox VE LXC Automation CLI")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML (default: config.yaml)")
    parser.add_argument("--env", default=".env", help="Path to .env file (default: .env)")
    parser.add_argument("--dry-run", action="store_true", help="Validate credentials and config without provisioning")
    parser.add_argument("--status", metavar="VMID", type=int, help="Check status of a specific container VMID")
    parser.add_argument("--start", metavar="VMID", type=int, help="Start a specific container VMID")
    parser.add_argument("--stop", metavar="VMID", type=int, help="Stop a specific container VMID")
    parser.add_argument("--destroy", metavar="VMID", type=int, help="Destroy a specific container VMID")
    args = parser.parse_args()

    env = load_env(args.env)
    config = load_config(args.config)
    client = get_client(env)
    node = config["proxmox"]["node"]

    if args.status:
        get_status(client, node, args.status)
        return
    if args.start:
        start_container(client, node, args.start)
        return
    if args.stop:
        stop_container(client, node, args.stop)
        return
    if args.destroy:
        destroy_container(client, node, args.destroy)
        return

    # Default action: preflight + create
    preflight_check(client, config)
    create_container(client, config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
