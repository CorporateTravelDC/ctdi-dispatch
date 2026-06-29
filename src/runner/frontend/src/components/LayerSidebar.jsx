/**
 * LayerSidebar — icon toggle strip for map overlay layers.
 * Sits as a floating vertical strip on the left edge of map views.
 * Each button toggles a key in useLayerConfig mapOverlays.
 * Wired via useGlobalLayerConfig from App context.
 */
import { useGlobalLayerConfig } from '../App.jsx'

const LAYERS = [
  { key: 'localFeed', icon: '✈', label: 'Local Feed',  title: 'Local ADS-B feeder aircraft (green)' },
  { key: 'tracked',   icon: '◎', label: 'Tracked',     title: 'Tracked/watchlisted flights (cyan)' },
  { key: 'tfr',       icon: '⊘', label: 'TFR Rings',   title: 'TFR restriction circles' },
  { key: 'airspace',  icon: '⬡', label: 'Airspace',    title: 'FRZ / SFRA boundaries' },
  { key: 'rings',     icon: '◯', label: 'Range Rings', title: '50 / 100 / 150 / 250 nm rings' },
  { key: 'trains',    icon: '⟹', label: 'Trains',      title: 'NEC train positions' },
  { key: 'marine',    icon: '⚓', label: 'Marine',      title: 'AIS vessel positions' },
  { key: 'weather',   icon: '☁', label: 'METAR',       title: 'DC-area METAR stations' },
]

export default function LayerSidebar() {
  const { config, toggleLayer } = useGlobalLayerConfig()
  if (!config) return null

  return (
    <aside
      className="layer-sidebar"
      aria-label="Map layer toggles"
      role="group"
    >
      {LAYERS.map(({ key, icon, label, title }) => {
        const active = !!config.layers[key]
        return (
          <button
            key={key}
            className={`ls-btn${active ? ' ls-on' : ' ls-off'}`}
            onClick={() => toggleLayer(key)}
            aria-pressed={active}
            aria-label={`${label}: ${active ? 'visible' : 'hidden'} — click to toggle`}
            title={title}
          >
            <span className="ls-icon" aria-hidden="true">{icon}</span>
            <span className="ls-label" aria-hidden="true">{label}</span>
          </button>
        )
      })}
    </aside>
  )
}
