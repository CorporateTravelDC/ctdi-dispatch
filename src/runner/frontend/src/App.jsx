import { Routes, Route, NavLink } from 'react-router-dom'
import { useState, useEffect, useCallback, createContext, useContext } from 'react'
import MapView from './components/MapView.jsx'
import StatusView from './components/StatusView.jsx'
import TfrView from './components/TfrView.jsx'
import BriefView from './components/BriefView.jsx'
import AdminView from './components/AdminView.jsx'
import SignalsView from './components/SignalsView.jsx'
import DispatchDrawer from './components/DispatchDrawer.jsx'
import CpsIndicator from './components/CpsIndicator.jsx'
import SettingsPanel from './components/SettingsPanel.jsx'
import OverviewView from './components/OverviewView.jsx'
import NtfyFeedView from './components/NtfyFeedView.jsx'
import IntelView from './components/IntelView.jsx'
import { useLayerConfig } from './hooks/useLayerConfig.js'

/** Global layer config context — allows any child to read/update panel visibility */
export const LayerConfigContext = createContext(null)
export function useGlobalLayerConfig() { return useContext(LayerConfigContext) }

function useSSE() {
  const [state, setState] = useState(null)
  useEffect(() => {
    const es = new EventSource('/api/stream')
    es.onmessage = (e) => {
      try { setState(JSON.parse(e.data)) } catch (_) {}
    }
    es.onerror = () => es.close()
    return () => es.close()
  }, [])
  return state
}

// Theme labels and icons for the 3-state cycle: auto → dark → light → auto
const THEME_STATES = [null, 'dark', 'light']
const THEME_LABELS = { null: '◑ AUTO', dark: '☾ DARK', light: '☀ LIGHT' }

export default function App() {
  const liveState  = useSSE()
  const [dispOpen, setDispOpen]       = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const layerCtx = useLayerConfig()

  // ── Theme management ──────────────────────────────────────────────────────
  const [themeOverride, setThemeOverride] = useState(
    () => localStorage.getItem('ctdc_theme') || null
  )

  useEffect(() => {
    const root = document.documentElement
    const apply = (t) => {
      root.setAttribute('data-theme', t)
      root.style.colorScheme = t
    }
    if (themeOverride) {
      apply(themeOverride)
    } else {
      const mq = window.matchMedia('(prefers-color-scheme: dark)')
      apply(mq.matches ? 'dark' : 'light')
      const handler = (e) => apply(e.matches ? 'dark' : 'light')
      mq.addEventListener('change', handler)
      return () => mq.removeEventListener('change', handler)
    }
  }, [themeOverride])

  const cycleTheme = useCallback(() => {
    setThemeOverride(prev => {
      const idx  = THEME_STATES.indexOf(prev)
      const next = THEME_STATES[(idx + 1) % THEME_STATES.length]
      if (next) localStorage.setItem('ctdc_theme', next)
      else      localStorage.removeItem('ctdc_theme')
      return next
    })
  }, [])

  // adsbMode is stored separately in localStorage (legacy key) for map view
  const [adsbMode, setAdsbMode] = useState(
    () => localStorage.getItem('adsbMode') || 'local'
  )
  const toggleAdsb = useCallback(() => {
    setAdsbMode(prev => {
      const next = prev === 'local' ? 'live' : 'local'
      localStorage.setItem('adsbMode', next)
      return next
    })
  }, [])

  return (
    <LayerConfigContext.Provider value={layerCtx}>
      {/* Skip navigation — visible on keyboard focus only */}
      <a href="#main-content" className="skip-nav">Skip to main content</a>

      <div className="app">
        <nav className="topbar" role="navigation" aria-label="Primary navigation">
          <span className="topbar-brand" aria-label="CS Executive Services Dispatch">
            CSEX DISPATCH
          </span>

          <div className="topbar-nav" role="menubar">
            <NavLink to="/" end
              className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}
              role="menuitem">OPS</NavLink>
            <NavLink to="/map"
              className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}
              role="menuitem">MAP</NavLink>
            <NavLink to="/status"
              className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}
              role="menuitem">STATUS</NavLink>
            <NavLink to="/tfr"
              className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}
              role="menuitem">TFR</NavLink>
            <NavLink to="/signals"
              className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}
              role="menuitem">SIGNALS</NavLink>
            <NavLink to="/brief"
              className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}
              role="menuitem">BRIEF</NavLink>
            <NavLink to="/feed"
              className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}
              role="menuitem">FEED</NavLink>
            <NavLink to="/intel"
              className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}
              role="menuitem">INTEL</NavLink>
            <button
              className={`nav-link disp-topbar-btn${dispOpen ? ' active' : ''}`}
              onClick={() => setDispOpen(o => !o)}
              aria-pressed={dispOpen}
              aria-label="Dispatch query panel"
              role="menuitem"
            >DISP</button>
            <NavLink to="/admin"
              className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}
              role="menuitem">ADMIN</NavLink>
          </div>

          <div className="topbar-right">
            <button
              className={`adsb-toggle ${adsbMode}`}
              onClick={toggleAdsb}
              aria-label={adsbMode === 'local'
                ? 'ADS-B: UltraFeeder local antenna — click to switch to live area'
                : 'ADS-B: airplanes.live full area — click to switch to local'}
              title={adsbMode === 'local' ? 'UltraFeeder (local antenna)' : 'airplanes.live (full area)'}
            >
              ADS-B:{adsbMode.toUpperCase()}
            </button>
            <CpsIndicator cps={liveState?.cps} />
            <button
              className="theme-btn"
              onClick={cycleTheme}
              aria-label={`Color theme: ${THEME_LABELS[String(themeOverride)] ?? THEME_LABELS['null']} — click to cycle`}
              title="Cycle theme: Auto / Dark / Light"
            >
              {THEME_LABELS[String(themeOverride)] ?? THEME_LABELS['null']}
            </button>
            <button
              className={`settings-btn${settingsOpen ? ' active' : ''}`}
              onClick={() => setSettingsOpen(o => !o)}
              aria-pressed={settingsOpen}
              aria-label="Panel visibility settings"
              title="Panel settings"
            >⚙</button>
          </div>
        </nav>

        {/* Settings panel — slide down from topbar */}
        {settingsOpen && (
          <SettingsPanel onClose={() => setSettingsOpen(false)} />
        )}

        <main className="content" id="main-content" tabIndex="-1">
          <Routes>
            <Route path="/" element={<OverviewView liveState={liveState} />} />
            <Route path="/map" element={<MapView adsbMode={adsbMode} liveState={liveState} />} />
            <Route path="/status" element={<StatusView liveState={liveState} />} />
            <Route path="/tfr" element={<TfrView />} />
            <Route path="/signals" element={<SignalsView />} />
            <Route path="/brief" element={<BriefView />} />
            <Route path="/feed" element={<NtfyFeedView />} />
            <Route path="/intel" element={<IntelView />} />
            <Route path="/admin" element={<AdminView />} />
          </Routes>
        </main>

        <DispatchDrawer liveState={liveState} open={dispOpen} setOpen={setDispOpen} />

        {/* Footer ticker */}
        <footer className="app-footer" role="contentinfo">
          <span className="app-footer-copy">© {new Date().getFullYear()} CS Executive Services, LLC</span>
          <span className="app-footer-sep">·</span>
          <a
            href="https://github.com/CorporateTravelDC/corporatetraveldc-dispatch-poc"
            target="_blank"
            rel="noopener noreferrer"
            className="app-footer-link"
            title="Source on GitHub (public mirror)"
          >
            GitHub ↗
          </a>
          <span className="app-footer-sep">·</span>
          <a
            href="https://dispatch.csexecutiveservices.com"
            className="app-footer-link"
          >
            dispatch.csexecutiveservices.com
          </a>
        </footer>
      </div>
    </LayerConfigContext.Provider>
  )
}
