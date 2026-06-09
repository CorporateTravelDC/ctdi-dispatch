import { Routes, Route, NavLink } from 'react-router-dom'
import { useState, useEffect, useCallback } from 'react'
import MapView from './components/MapView.jsx'
import StatusView from './components/StatusView.jsx'
import TfrView from './components/TfrView.jsx'
import BriefView from './components/BriefView.jsx'
import AdminView from './components/AdminView.jsx'
import SignalsView from './components/SignalsView.jsx'
import CpsIndicator from './components/CpsIndicator.jsx'

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

export default function App() {
  const liveState = useSSE()
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
    <div className="app">
      <nav className="topbar">
        <span className="topbar-brand">CSEX DISPATCH</span>
        <div className="topbar-nav">
          <NavLink to="/" end className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}>MAP</NavLink>
          <NavLink to="/status" className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}>STATUS</NavLink>
          <NavLink to="/tfr" className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}>TFR</NavLink>
          <NavLink to="/signals" className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}>SIGNALS</NavLink>
          <NavLink to="/brief" className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}>BRIEF</NavLink>
          <NavLink to="/admin" className={({isActive}) => isActive ? 'nav-link active' : 'nav-link'}>ADMIN</NavLink>
        </div>
        <div className="topbar-right">
          <button
            className={`adsb-toggle ${adsbMode}`}
            onClick={toggleAdsb}
            title={adsbMode === 'local' ? 'UltraFeeder (local antenna)' : 'airplanes.live (full area)'}
          >
            ADS-B: {adsbMode.toUpperCase()}
          </button>
          <CpsIndicator cps={liveState?.cps} />
        </div>
      </nav>
      <main className="content">
        <Routes>
          <Route path="/" element={<MapView adsbMode={adsbMode} liveState={liveState} />} />
          <Route path="/status" element={<StatusView liveState={liveState} />} />
          <Route path="/tfr" element={<TfrView />} />
          <Route path="/signals" element={<SignalsView />} />
          <Route path="/brief" element={<BriefView />} />
          <Route path="/admin" element={<AdminView />} />
        </Routes>
      </main>
    </div>
  )
}
