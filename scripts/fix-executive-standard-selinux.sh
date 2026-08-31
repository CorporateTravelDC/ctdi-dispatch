#!/usr/bin/env bash
set -euo pipefail
semanage fcontext -a -t httpd_sys_content_t '/var/lib/corporatetraveldc/executive-standard-site(/.*)?'
restorecon -Rv /var/lib/corporatetraveldc/executive-standard-site
echo "Done."
