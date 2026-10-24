# Implementation Plan: Proxmox VE VM (QEMU/KVM) → LXC Container Automation

---

## Goal

Create a local Proxmox VE virtual machine using QEMU/KVM + libvirt, then provision an unprivileged Debian 12 LXC container inside it — all automated from this repository.

---

## Steps

### Step 1 — Verify Host Prerequisites

Check that the local machine supports KVM and has the required packages.

**File:** `setup_proxmox_vm.sh` (first section)

**Actions:**
- Check CPU virtualization support (`grep -c vmx /proc/cpuinfo` or `kvm-ok`)
- Verify nested virtualization is enabled (`/sys/module/kvm_intel/parameters/nested`)
- Confirm packages are installed: `qemu-kvm`, `libvirt-daemon-system`, `virtinst`, `bridge-utils`, `wget`
- Confirm default libvirt network is active (`virsh net-list`)
- Confirm sufficient disk space (~32 GB free)

**Exit criteria:** All checks pass, script prints ✓ for each.

---

### Step 2 — Download Proxmox VE ISO

Fetch the PVE installer ISO if not already cached.

**File:** `setup_proxmox_vm.sh`

**Actions:**
- Define ISO URL: `https://enterprise.proxmox.com/iso/proxmox-ve_8.4-1.iso`
- Download to `/var/lib/libvirt/images/proxmox-ve.iso` (skip if file exists and size matches)
- Verify download integrity (checksum)

**Exit criteria:** ISO file exists at expected path.

---

### Step 3 — Create Proxmox VE VM via `virt-install`

Spin up the VM that will become our Proxmox host.

**File:** `setup_proxmox_vm.sh`

**VM Specs:**
| Setting | Value |
|---|---|
| Name | `proxmox-ve` |
| vCPUs | 4 |
| RAM | 8192 MB |
| Disk | 64 GB qcow2 (`/var/lib/libvirt/images/proxmox-ve.qcow2`) |
| CPU | `host-passthrough` (nested virt) |
| Network | `network=default,model=virtio` (NAT via `virbr0`) |
| Graphics | VNC |
| OS variant | `debian12` |

**Actions:**
- Check if VM `proxmox-ve` already exists (`virsh dominfo`)
- Run `virt-install` with the specs above and `--noautoconsole`
- Print VNC connection info (`virsh vncdisplay proxmox-ve`)

**Exit criteria:** VM is running, VNC console accessible.

---

### Step 4 — Complete Proxmox VE Installation (Manual)

> [!IMPORTANT]
> This step is interactive. The script pauses and waits for user confirmation.

**Actions (user does via VNC):**
1. Connect to VNC console (address printed by Step 3)
2. Walk through the PVE installer wizard:
   - Accept EULA
   - Select target disk
   - Set country/timezone/keyboard
   - Set root password and email
   - Configure network (management interface, hostname, IP — or DHCP)
3. Installer reboots the VM
4. User presses Enter in the script to continue

**Exit criteria:** PVE VM has rebooted and shows the login prompt / web UI URL.

---

### Step 5 — Detect VM IP & Wait for PVE API

After PVE install and reboot, discover the VM's IP and wait for the API to become ready.

**File:** `setup_proxmox_vm.sh`

**Actions:**
- Get VM IP: `virsh domifaddr proxmox-ve`
- Poll `https://<VM_IP>:8006/api2/json/version` until HTTP 200 (with timeout)
- Print PVE version info

**Exit criteria:** API responds with valid JSON containing PVE version.

---

### Step 6 — Create API Token on PVE VM

SSH into the PVE VM and create a Proxmox API token for automation.

**File:** `setup_proxmox_vm.sh`

**Actions:**
- Accept SSH host key: `ssh-keyscan -H $VM_IP >> ~/.ssh/known_hosts`
- SSH in and create token:
  ```bash
  ssh root@$VM_IP "pveum user token add root@pam lxc-token --privsep 0"
  ```
- Parse and capture the token secret from command output

**Exit criteria:** Token ID and secret captured in variables.

---

### Step 7 — Download Debian 12 Template on PVE VM

Pre-download the LXC container template so Phase 2 doesn't have to wait.

**File:** `setup_proxmox_vm.sh`

**Actions:**
- SSH in and run:
  ```bash
  ssh root@$VM_IP "pveam update && pveam download local debian-12-standard_12.7-1_amd64.tar.zst"
  ```
- Verify template appears in storage:
  ```bash
  ssh root@$VM_IP "pveam list local"
  ```

**Exit criteria:** Debian 12 template listed in `local` storage.

---

### Step 8 — Generate `.env` File

Write the Proxmox connection credentials for the Python script.

**File:** `setup_proxmox_vm.sh` (generates `.env`)

**Actions:**
- Write `.env` with:
  ```env
  PROXMOX_HOST=<detected VM IP>
  PROXMOX_PORT=8006
  PROXMOX_USER=root@pam
  PROXMOX_TOKEN_NAME=lxc-token
  PROXMOX_TOKEN_VALUE=<captured token secret>
  PROXMOX_VERIFY_SSL=false
  ```
- Print success message with path to `.env`

**Exit criteria:** `.env` file exists in project root with valid credentials.

---

### Step 9 — Write Python Dependencies & Config Files

Create the supporting files for the LXC automation script.

**Files:**
- `requirements.txt`:
  ```txt
  requests>=2.31.0
  pyyaml>=6.0.1
  ```
- `config.example.yaml`:
  ```yaml
  proxmox:
    node: "pve"
    storage: "local-lvm"
    template_storage: "local"

  container:
    vmid: null
    hostname: "debian12-lxc"
    ostemplate: "debian-12-standard_12.7-1_amd64.tar.zst"
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
  ```
- `.env.example` (template version of `.env`)

**Exit criteria:** All three files created.

---

### Step 10 — Write `create_lxc.py`

The main automation script that talks to the Proxmox REST API.

**File:** `create_lxc.py`

**Components:**
1. **`ProxmoxClient` class** — HTTP client with `PVEAPIToken` auth, SSL handling
2. **Pre-flight checks** — Validate API connectivity (`/version`), node name, template existence
3. **Container creation** — Auto-fetch VMID (`/cluster/nextid`), `POST /nodes/{node}/lxc`, poll UPID task
4. **Boot & IP discovery** — Start container, query `/nodes/{node}/lxc/{vmid}/interfaces`
5. **CLI flags** — `--dry-run`, `--status <vmid>`, `--start <vmid>`, `--stop <vmid>`, `--destroy <vmid>`

**Exit criteria:** `python3 -m py_compile create_lxc.py` passes.

---

### Step 11 — Dry-Run Validation

Test the full pipeline without creating anything.

**Commands:**
```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
python3 create_lxc.py --dry-run
```

**Exit criteria:** Script reports successful API auth, correct PVE version, node found, template found — no container created.

---

### Step 12 — Create LXC Container (Live)

Run the actual container provisioning.

**Commands:**
```bash
python3 create_lxc.py
```

**Exit criteria:** Script reports container created, started, and prints assigned IP.

---

### Step 13 — Verify Container

Confirm the container is running and reachable.

**Commands:**
```bash
# Check status via API
python3 create_lxc.py --status <vmid>

# Check from inside PVE VM
ssh root@<VM_IP> "pct status <vmid>"
ssh root@<VM_IP> "pct exec <vmid> -- ip a"
ssh root@<VM_IP> "pct exec <vmid> -- cat /etc/os-release"
```

**Exit criteria:** Container is `running`, has an IP, reports Debian 12.

---

### Step 14 — Write `README.md`

Document the full setup for future reference.

**File:** `README.md`

**Sections:**
- Overview & architecture diagram
- Prerequisites
- Quickstart (end-to-end)
- Phase 1: VM setup details
- Phase 2: LXC automation details
- CLI reference (`create_lxc.py` flags)
- Troubleshooting
- Cleanup / teardown commands

**Exit criteria:** README covers all steps and is self-contained.

---

## Step Summary

| Step | What | Type | File(s) |
|---:|---|---|---|
| 1 | Verify host prerequisites | Auto | `setup_proxmox_vm.sh` |
| 2 | Download PVE ISO | Auto | `setup_proxmox_vm.sh` |
| 3 | Create VM via virt-install | Auto | `setup_proxmox_vm.sh` |
| 4 | Complete PVE installer | **Manual (VNC)** | — |
| 5 | Detect VM IP & wait for API | Auto | `setup_proxmox_vm.sh` |
| 6 | Create API token on PVE | Auto | `setup_proxmox_vm.sh` |
| 7 | Download Debian 12 template | Auto | `setup_proxmox_vm.sh` |
| 8 | Generate `.env` file | Auto | `setup_proxmox_vm.sh` |
| 9 | Write deps & config files | Code | `requirements.txt`, `config.example.yaml`, `.env.example` |
| 10 | Write `create_lxc.py` | Code | `create_lxc.py` |
| 11 | Dry-run validation | Test | — |
| 12 | Create LXC container | Live | — |
| 13 | Verify container | Test | — |
| 14 | Write README | Docs | `README.md` |
