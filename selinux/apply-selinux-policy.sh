#!/usr/bin/env bash
# =============================================================================
# selinux/apply-selinux-policy.sh
# CS Executive Services -- SELinux policy remediation + directory bootstrap
#
# Run as root before starting any corporatetraveldc services.
# Idempotent -- safe to re-run after package updates or Pi migration.
#
# Usage:
#   sudo ./selinux/apply-selinux-policy.sh [--raw-image-dir <path>] [--dry-run]
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=false
RAW_IMAGE_DIR="${HOME}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --raw-image-dir) RAW_IMAGE_DIR="$2"; shift 2 ;;
        *) echo "[FAIL] Unknown argument: $1" >&2; exit 1 ;;
    esac
done

run() {
    if [[ "$DRY_RUN" == true ]]; then echo "[DRY]  $*"; else "$@"; fi
}

require_root() {
    if [[ "$EUID" -ne 0 ]]; then
        echo "[FAIL] Must be run as root." >&2; exit 1
    fi
}

check_deps() {
    local missing=()
    for cmd in semodule checkmodule semanage restorecon; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "[INFO] Installing missing tools: ${missing[*]}"
        run dnf install -y policycoreutils-python-utils checkpolicy
    fi
}

build_and_load_module() {
    local name="$1"
    local te_src="${SCRIPT_DIR}/${name}.te"
    [[ -f "$te_src" ]] || { echo "[FAIL] Missing: ${te_src}" >&2; return 1; }

    local work_dir
    work_dir="$(mktemp -d /tmp/selinux-${name}-XXXXXX)"
    trap "rm -rf ${work_dir}" RETURN

    cp "${te_src}" "${work_dir}/${name}.te"
    echo "[INFO] Compiling: ${name}"
    run checkmodule -M -m -o "${work_dir}/${name}.mod" "${work_dir}/${name}.te"
    run semodule_package -o "${work_dir}/${name}.pp" -m "${work_dir}/${name}.mod"

    if semodule -l 2>/dev/null | grep -q "^${name}$"; then
        run semodule -u "${work_dir}/${name}.pp"
    else
        run semodule -i "${work_dir}/${name}.pp"
    fi
    echo "[OK]  ${name}"
}

label_container_path() {
    local path="$1"
    local owner="${2:-corporatetraveldc:corporatetraveldc}"
    local mode="${3:-0755}"
    echo "[INFO] Labeling: ${path}"
    run mkdir -p "${path}"
    run chown "${owner}" "${path}"
    run chmod "${mode}" "${path}"
    run semanage fcontext -a -t container_file_t "${path}(/.*)?" 2>/dev/null \
        || run semanage fcontext -m -t container_file_t "${path}(/.*)?
"
    run restorecon -Rv "${path}"
    echo "[OK]  ${path}"
}

require_root
check_deps

echo "=== CS Executive Services -- SELinux Policy Apply ==="
echo "[INFO] Dry run: ${DRY_RUN}"
echo ""

# ---------------------------------------------------------------------------
# Step 1 -- Runtime directory (/run/corporatetraveldc)
# Recreated on each boot via tmpfiles.d; also ensure it exists now.
# ---------------------------------------------------------------------------
echo "--- Step 1: /run/corporatetraveldc ---"
run mkdir -p /run/corporatetraveldc
run chown corporatetraveldc:corporatetraveldc /run/corporatetraveldc
run chmod 755 /run/corporatetraveldc
echo "[OK]  /run/corporatetraveldc"

# ---------------------------------------------------------------------------
# Step 2 -- Data directory (/var/lib/corporatetraveldc)
# ---------------------------------------------------------------------------
echo "--- Step 2: /var/lib/corporatetraveldc ---"
label_container_path "/var/lib/corporatetraveldc"
label_container_path "/var/lib/corporatetraveldc/acarshub"

# ---------------------------------------------------------------------------
# Step 3 -- Config directory (/etc/corporatetraveldc)
# Read-only mounts -- owned root:corporatetraveldc, mode 640 on files.
# ---------------------------------------------------------------------------
echo "--- Step 3: /etc/corporatetraveldc ---"
run mkdir -p /etc/corporatetraveldc
run chown root:corporatetraveldc /etc/corporatetraveldc
run chmod 750 /etc/corporatetraveldc
run semanage fcontext -a -t container_file_t "/etc/corporatetraveldc(/.*)?" 2>/dev/null \
    || run semanage fcontext -m -t container_file_t "/etc/corporatetraveldc(/.*)?
"
run restorecon -Rv /etc/corporatetraveldc
echo "[OK]  /etc/corporatetraveldc"

# ---------------------------------------------------------------------------
# Step 4 -- ntfy directories
# ---------------------------------------------------------------------------
echo "--- Step 4: ntfy directories ---"
label_container_path "/var/lib/ntfy"
# /etc/ntfy is read-only config -- label but keep root ownership
run mkdir -p /etc/ntfy
run semanage fcontext -a -t container_file_t "/etc/ntfy(/.*)?" 2>/dev/null \
    || run semanage fcontext -m -t container_file_t "/etc/ntfy(/.*)?
"
run restorecon -Rv /etc/ntfy
echo "[OK]  /etc/ntfy"

# ---------------------------------------------------------------------------
# Step 5 -- Relabel .raw image files to virt_image_t
# ---------------------------------------------------------------------------
echo "--- Step 5: virt_image_t relabel for .raw files ---"
mapfile -t raw_files < <(find "${RAW_IMAGE_DIR}" -maxdepth 3 -name "*.raw" -type f 2>/dev/null)
if [[ ${#raw_files[@]} -eq 0 ]]; then
    echo "[SKIP] No .raw files found under ${RAW_IMAGE_DIR}"
else
    for f in "${raw_files[@]}"; do
        echo "[INFO] Relabeling: ${f}"
        run chcon -t virt_image_t "${f}"
    done
    run semanage fcontext -a -t virt_image_t "${RAW_IMAGE_DIR}/[^/]*\.raw" 2>/dev/null \
        || run semanage fcontext -m -t virt_image_t "${RAW_IMAGE_DIR}/[^/]*\.raw"
fi

# ---------------------------------------------------------------------------
# Step 6 -- tailscaled policy
# ---------------------------------------------------------------------------
echo "--- Step 6: tailscaled policy ---"
if seinfo -t 2>/dev/null | grep -q "tailscaled_t"; then
    echo "[OK]  upstream tailscaled_t present"
    if semodule -l 2>/dev/null | grep -q "^csexec-tailscaled$"; then
        run semodule -r csexec-tailscaled
    fi
else
    if dnf info tailscale-selinux &>/dev/null; then
        run dnf install -y tailscale-selinux
        run restorecon -v "$(command -v tailscaled 2>/dev/null || echo /usr/sbin/tailscaled)"
    else
        build_and_load_module "csexec-tailscaled"
    fi
fi

# ---------------------------------------------------------------------------
# Step 7 -- TE modules
# ---------------------------------------------------------------------------
echo "--- Step 7: TE modules ---"
build_and_load_module "csexec-virtqemud"
build_and_load_module "csexec-logind-userns"

# ---------------------------------------------------------------------------
# Step 8 -- Verify
# ---------------------------------------------------------------------------
echo ""
echo "--- Step 8: Verify ---"
for mod in csexec-virtqemud csexec-logind-userns; do
    semodule -l 2>/dev/null | grep -q "^${mod}$" \
        && echo "[OK]  module: ${mod}" \
        || echo "[FAIL] module: ${mod}" >&2
done

for path in /var/lib/corporatetraveldc /var/lib/ntfy /etc/corporatetraveldc /etc/ntfy /run/corporatetraveldc; do
    [[ -d "${path}" ]] \
        && echo "[OK]  exists: ${path}" \
        || echo "[FAIL] missing: ${path}" >&2
done

echo ""
echo "[OK]  Apply complete."
echo "[INFO] Restart tailscaled if it was previously blocked: sudo systemctl restart tailscaled"
