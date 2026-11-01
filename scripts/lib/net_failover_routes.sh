# Shared route operations for the unprivileged failover watchdog.
# Profile persistence and the route actually in use must both agree.
iface_connected() {
    local state
    state=$(nmcli -g GENERAL.STATE device show "$1" 2>/dev/null) || return 1
    [[ "$state" =~ ^100([^0-9]|$) ]]
}

iface_has_carrier() {
    local root="${NET_FAILOVER_SYS_NET_DIR:-/sys/class/net}"
    [[ "$(cat "${root}/$1/carrier" 2>/dev/null)" == 1 ]] || iface_connected "$1"
}

current_default_iface() {
    ip -4 route get 1.1.1.1 2>/dev/null |
        awk 'NR==1 {for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}'
}

configured_metric() {
    nmcli -g ipv4.route-metric connection show "$1" 2>/dev/null
}

backup_profile_is_active() {
    local actual expected
    actual=$(nmcli -g GENERAL.CON-UUID device show "$BACKUP_IF" 2>/dev/null) || return 1
    expected=$(nmcli -g connection.uuid connection show "$BACKUP_CON" 2>/dev/null) || return 1
    [[ -n "$expected" && "$actual" == "$expected" ]]
}

wait_for_route() {
    local expected="$1" attempt
    for attempt in 1 2 3 4 5; do
        [[ "$(current_default_iface)" == "$expected" ]] && return 0
        sleep 1
    done
    log error "route verification failed: expected ${expected}, actual $(current_default_iface)"
    return 1
}

apply_backup_metric() {
    local metric="$1" mode="${2:-}" expected="$BACKUP_IF"
    [[ "$mode" == failback ]] && expected="$PRIMARY_IF"
    if iface_connected "$BACKUP_IF" && ! backup_profile_is_active; then
        log error "active backup profile differs from configured profile"; return 1
    fi
    nmcli connection modify "$BACKUP_CON" ipv4.route-metric "$metric" >/dev/null 2>&1 || {
        log error "backup profile metric update failed"; return 1;
    }
    if iface_connected "$BACKUP_IF"; then
        # Reapply updates the active connection without a disconnect/reconnect.
        nmcli device reapply "$BACKUP_IF" >/dev/null 2>&1 || {
            log error "backup live metric reapply failed"; return 1;
        }
        backup_profile_is_active || { log error "backup profile changed during reapply"; return 1; }
    elif [[ "$mode" == failback ]] && ! iface_has_carrier "$BACKUP_IF"; then
        log info "backup inactive: saved profile metric; verifying primary route"
    else
        nmcli connection up "$BACKUP_CON" >/dev/null 2>&1 || {
            log error "backup activation failed"; return 1;
        }
    fi
    wait_for_route "$expected"
}
