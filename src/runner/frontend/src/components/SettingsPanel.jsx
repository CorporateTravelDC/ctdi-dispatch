/**
 * SettingsPanel — slide-down panel from topbar
 * Controls layer visibility + optional backend sync token
 */
import { useState } from 'react'
import { useGlobalLayerConfig } from '../App.jsx'
import { hasSyncToken, setSyncToken } from '../hooks/useLayerConfig.js'

const LAYER_LABELS = {
  vdl2:  { label: 'VDL2',  color: '#00d4ff', desc: 'VDL2 ACARS messages' },
  acars: { label: 'ACARS', color: '#ffd700', desc: 'ACARS uplink/downlink' },
  hfdl:  { label: 'HFDL',  color: '#39ff14', desc: 'HF data link' },
  ais:   { label: 'AIS',   color: '#4a9eff', desc: 'Marine vessel tracking' },
  metar: { label: 'METAR', color: '#87ceeb', desc: 'DC-area weather stations' },
}

export default function SettingsPanel({ onClose }) {
  const { config, toggleLayer, setAllLayers } = useGlobalLayerConfig()
  const [syncToken, setSyncTokenLocal] = useState(localStorage.getItem('ctdc_admin_token') || '')
  const [syncStatus, setSyncStatus] = useState(hasSyncToken() ? 'active' : 'off')

  function handleSyncSave() {
    const t = syncToken.trim()
    setSyncToken(t)
    setSyncStatus(t ? 'active' : 'off')
  }

  function handleSyncClear() {
    setSyncTokenLocal('')
    setSyncToken(null)
    setSyncStatus('off')
  }

  const allOn  = Object.values(config.layers).every(Boolean)
  const allOff = Object.values(config.layers).every(v => !v)

  return (
    <div className="settings-panel" role="dialog" aria-label="Panel visibility settings" aria-modal="true">
      <div className="settings-inner">

        {/* Header */}
        <div className="settings-header">
          <span className="settings-title">PANEL SETTINGS</span>
          <button
            className="settings-close"
            onClick={onClose}
            aria-label="Close settings"
          >✕</button>
        </div>

        {/* Layer toggles */}
        <section className="settings-section" aria-labelledby="layers-heading">
          <h3 id="layers-heading" className="settings-section-title">
            SIGNAL LAYERS
            <span className="settings-section-hint">Saved automatically</span>
          </h3>
          <div className="layer-toggles" role="group" aria-label="Signal panel toggles">
            {Object.entries(LAYER_LABELS).map(([key, meta]) => {
              const active = config.layers[key]
              return (
                <button
                  key={key}
                  className={`layer-toggle-btn${active ? ' on' : ' off'}`}
                  style={active ? { borderColor: meta.color, color: meta.color } : undefined}
                  onClick={() => toggleLayer(key)}
                  aria-pressed={active}
                  aria-label={`${meta.label} — ${meta.desc} — ${active ? 'visible' : 'hidden'}`}
                  title={meta.desc}
                >
                  <span className="layer-toggle-indicator" aria-hidden="true">
                    {active ? '●' : '○'}
                  </span>
                  {meta.label}
                </button>
              )
            })}
          </div>

          <div className="layer-bulk-row">
            <button
              className="layer-bulk-btn"
              onClick={() => setAllLayers(true)}
              disabled={allOn}
              aria-label="Show all signal panels"
            >ALL ON</button>
            <button
              className="layer-bulk-btn"
              onClick={() => setAllLayers(false)}
              disabled={allOff}
              aria-label="Hide all signal panels"
            >ALL OFF</button>
          </div>
        </section>

        {/* Backend sync */}
        <section className="settings-section" aria-labelledby="sync-heading">
          <h3 id="sync-heading" className="settings-section-title">
            CROSS-DEVICE SYNC
            <span className={`sync-status-chip ${syncStatus}`}>
              {syncStatus === 'active' ? 'ACTIVE' : 'OFF'}
            </span>
          </h3>
          <p className="settings-hint">
            Enter your admin token to sync settings across browsers/devices.
            Without a token, settings are saved locally in this browser only.
          </p>
          <div className="sync-row">
            <input
              type="password"
              className="sync-token-input"
              value={syncToken}
              onChange={e => setSyncTokenLocal(e.target.value)}
              placeholder="ctdc_..."
              aria-label="Admin sync token"
              autoComplete="current-password"
            />
            <button
              className="sync-save-btn"
              onClick={handleSyncSave}
              aria-label="Save sync token"
            >SAVE</button>
            {syncStatus === 'active' && (
              <button
                className="sync-clear-btn"
                onClick={handleSyncClear}
                aria-label="Clear sync token and disable cross-device sync"
              >CLEAR</button>
            )}
          </div>
        </section>

      </div>
    </div>
  )
}
