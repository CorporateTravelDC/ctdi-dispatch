#!/usr/bin/env bash
# =============================================================================
# selinux/apply-selinux-policy.sh
# CS Executive Services -- SELinux policy remediation script
#
# Covers:
#   1. virtqemud .raw image file context (relabel to virt_image_t)
#   2. tailscaled SELinux policy (upstream if available; custom fallback)
#   3. systemd-logind cap_userns sys_ptrace
#   4. /var/lib/corporatetraveldc container_file_t labeling
#   5. /run/corporatetraveldc runtime directory
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

require_root
check_deps

echo "=== CS Executive Services -- SELinux Policy Apply ==="

# -- Step 1: Relabel .raw image files --
echo "--- Step 1: virt_image_t relabel ---"
mapfile -t raw_files < <(find "${RAW_IMAGE_DIR}" -maxdepth 3 -name "*.raw" -type f 2>/dev/null)
if [[ ${#raw_files[@]} -eq 0 ]]; then
    echo "[SKIP] No .raw files found"
else
    for f in "${raw_files[@]}"; do
        echo "[INFO] Relabeling: ${f}"
        run chcon -t virt_image_t "${f}"
    done
    run semanage fcontext -a -t virt_image_t "${RAW_IMAGE_DIR}/[^/]*\.raw" 2>/dev/null \
        || run semanage fcontext -m -t virt_image_t "${RAW_IMAGE_DIR}/[^/]*\.raw"
fi

# -- Step 2: /var/lib/corporatetraveldc --
echo "--- Step 2: container_file_t on var/lib ---"
run mkdir -p /var/lib/corporatetraveldc
run chown -R corporatetraveldc:corporatetraveldc /var/lib/corporatetraveldc
run semanage fcontext -a -t container_file_t "/var/lib/corporatetraveldc(/.*)?" 2>/dev/null \
    || run semanage fcontext -m -t container_file_t "/var/lib/corporatetraveldc(/.*)?"
run restorecon -Rv /var/lib/corporatetraveldc

# -- Step 3: /run/corporatetraveldc --
echo "--- Step 3: /run/corporatetraveldc ---"
run mkdir -p /run/corporatetraveldc
run chown corporatetraveldc:corporatetraveldc /run/corporatetraveldc
run chmod 755 /run/corporatetraveldc

# -- Step 4: tailscaled --
echo "--- Step 4: tailscaled policy ---"
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

# -- Step 5: TE modules --
echo "--- Step 5: TE modules ---"
build_and_load_module "csexec-virtqemud"
build_and_load_module "csexec-logind-userns"

# -- Step 6: Verify --
echo "--- Step 6: Verify ---"
for mod in csexec-virtqemud csexec-logind-userns; do
    semodule -l 2>/dev/null | grep -q "^${mod}$" \
        && echo "[OK]  ${mod}" \
        || echo "[FAIL] ${mod}" >&2
done

echo "[OK]  Apply complete -- restart tailscaled if needed"
