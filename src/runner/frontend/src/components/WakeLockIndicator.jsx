// Small topbar pill mirroring CpsIndicator's pattern. Added 2026-08-02
// alongside useWakeLock — the one state worth actually alarming on is
// 'lost' (nogo/red), since that's the "screen could go dark without
// anyone noticing" case. 'active' is deliberately the quietest state
// (muted, no glow) so a working wake lock doesn't compete for attention
// with the CPS indicator next to it; 'lost'/'denied' are the states meant
// to catch your eye.
export default function WakeLockIndicator({ status }) {
  const CONFIG = {
    active:      { label: 'AWAKE',  color: 'quiet',   title: 'Screen wake lock active — display will not sleep' },
    lost:        { label: 'LOST',   color: 'nogo',    title: 'Wake lock was released while still visible and is being re-acquired — screen may have dimmed. Check the device.' },
    denied:      { label: 'DENIED', color: 'nogo',    title: 'Wake lock request was denied (battery saver or permissions policy) — screen may sleep normally' },
    pending:     { label: 'WAKE…',  color: 'unknown', title: 'Requesting screen wake lock…' },
    unsupported: null, // nothing to show -- don't clutter the topbar on browsers that lack the API
  }

  const cfg = CONFIG[status]
  if (!cfg) return null

  return (
    <span className={`cps-pill wakelock-pill ${cfg.color}`} title={cfg.title}>
      {cfg.label}
    </span>
  )
}
