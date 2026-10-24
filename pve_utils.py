#!/usr/bin/env python3
"""
pve_utils.py - Shared utilities for Proxmox VE SSH operations and environment loading.
"""

import os
import sys
import paramiko
from create_lxc import load_env


def get_ssh_client(env=None):
    """Returns an authenticated paramiko SSHClient to the Proxmox host."""
    if env is None:
        env = load_env()

    host = os.getenv("PROXMOX_HOST", env.get("PROXMOX_HOST", "127.0.0.1"))
    port = int(os.getenv("PROXMOX_SSH_PORT", env.get("PROXMOX_SSH_PORT", "2222")))
    user = os.getenv("PROXMOX_SSH_USER", env.get("PROXMOX_SSH_USER", "root"))
    password = os.getenv("PROXMOX_SSH_PASSWORD", env.get("PROXMOX_SSH_PASSWORD", "12345678"))
    key_file = os.getenv("PROXMOX_SSH_KEY", env.get("PROXMOX_SSH_KEY", "id_pve"))

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # Try SSH key first if it exists
    if os.path.exists(key_file):
        try:
            client.connect(host, port=port, username=user, key_filename=key_file, timeout=10)
            return client
        except Exception:
            pass

    # Fallback to password authentication
    try:
        client.connect(host, port=port, username=user, password=password, timeout=10)
        return client
    except Exception as e:
        print(f"Failed to connect to Proxmox VE via SSH ({host}:{port}): {e}", file=sys.stderr)
        raise


def exec_ssh(client, cmd, check=True):
    """Executes a command on the Proxmox host via SSH."""
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode()
    err = stderr.read().decode()
    exit_code = stdout.channel.recv_exit_status()
    if check and exit_code != 0:
        print(f"Command failed (exit {exit_code}): {cmd}\nSTDOUT: {out}\nSTDERR: {err}", file=sys.stderr)
        raise RuntimeError(f"Command failed with exit {exit_code}: {cmd}")
    return out, err, exit_code


def exec_in_lxc(client, vmid, cmd, check=True):
    """Executes a command inside an LXC container using pct exec."""
    wrapped = f"pct exec {vmid} -- bash -c {repr(cmd)}"
    return exec_ssh(client, wrapped, check=check)
