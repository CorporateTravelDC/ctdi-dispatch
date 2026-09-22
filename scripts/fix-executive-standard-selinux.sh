#!/usr/bin/env bash
set -euo pipefail
semanage fcontext -a -t httpd_sys_content_t '/var/lib/corporatetraveldc/executive-standard-site(/.*)?'
# -F: force -- without it, restorecon treats the existing container_file_t
# labeling (set for the podman-mounted paths elsewhere under
# /var/lib/corporatetraveldc/) as admin-customized and refuses to override
# it even though our more specific fcontext rule above matches. Confirmed
# live 2026-08-31: plain `restorecon -Rv` printed "not reset as customized
# by admin" for every single file and changed nothing.
restorecon -RvF /var/lib/corporatetraveldc/executive-standard-site
echo "Done."
