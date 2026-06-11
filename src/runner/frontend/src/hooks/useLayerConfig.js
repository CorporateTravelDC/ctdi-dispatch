/**
 * useLayerConfig — persistent layer/panel visibility config
 *
 * Stores to localStorage under 'ctdc_layer_config'.
 * Optional backend sync: if CTDC_TOKEN is set in localStorage, the config
 * is also POSTed to /api/dispatch/api/v1/config so it survives cross-device.
 * Backend sync is best-effort and never blocks the UI.
 */
import { useState, useCallback, useEffect, useRef } from 'react'

export const DEFAULT_CONFIG = {
  layers: {
    vdl2:  true,
    acars: true,
    hfdl:  true,
    ais:   true,
    metar: true,
  },
  // signal panel order (future use)
  // mapOverlays: { tfr: true, aircraft: true, airspace: true },
}

const STORAGE_KEY = 'ctdc_layer_config'
const TOKEN_KEY   = 'ctdc_admin_token'

function loadConfig() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_CONFIG
    const parsed = JSON.parse(raw)
    // Deep-merge: keep defaults for any keys not present in stored config
    return {
      ...DEFAULT_CONFIG,
      ...parsed,
      layers: { ...DEFAULT_CONFIG.layers, ...(parsed.layers || {}) },
    }
  } catch {
    return DEFAULT_CONFIG
  }
}

async function syncToBackend(cfg) {
  const token = localStorage.getItem(TOKEN_KEY)
  if (!token) return
  try {
    await fetch('/api/dispatch/api/v1/config', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify(cfg),
    })
  } catch (_) {
    // best-effort — never throw
  }
}

async function loadFromBackend() {
  const token = localStorage.getItem(TOKEN_KEY)
  if (!token) return null
  try {
    const r = await fetch('/api/dispatch/api/v1/config', {
      headers: { 'Authorization': `Bearer ${token}` },
    })
    if (!r.ok) return null
    return await r.json()
  } catch {
    return null
  }
}

export function useLayerConfig() {
  const [config, setConfig] = useState(loadConfig)
  const initialSync = useRef(false)

  // On mount: try to load from backend (if token present) — backend wins over local
  useEffect(() => {
    if (initialSync.current) return
    initialSync.current = true
    loadFromBackend().then(remote => {
      if (!remote) return
      const merged = {
        ...DEFAULT_CONFIG,
        ...remote,
        layers: { ...DEFAULT_CONFIG.layers, ...(remote.layers || {}) },
      }
      setConfig(merged)
      localStorage.setItem(STORAGE_KEY, JSON.stringify(merged))
    })
  }, [])

  const updateConfig = useCallback((updates) => {
    setConfig(prev => {
      const next = { ...prev, ...updates }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      syncToBackend(next)
      return next
    })
  }, [])

  const toggleLayer = useCallback((key) => {
    setConfig(prev => {
      const next = {
        ...prev,
        layers: { ...prev.layers, [key]: !prev.layers[key] },
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      syncToBackend(next)
      return next
    })
  }, [])

  const setAllLayers = useCallback((value) => {
    setConfig(prev => {
      const next = {
        ...prev,
        layers: Object.fromEntries(Object.keys(prev.layers).map(k => [k, value])),
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      syncToBackend(next)
      return next
    })
  }, [])

  return { config, updateConfig, toggleLayer, setAllLayers }
}

/** Returns true if the user has a stored admin token (sync mode active) */
export function hasSyncToken() {
  return !!localStorage.getItem(TOKEN_KEY)
}

/** Store or clear admin token for backend sync */
export function setSyncToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}
