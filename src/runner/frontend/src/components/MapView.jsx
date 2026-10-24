// 2026-10-24: thin wrapper. The Globe (aggregator iframe) mode, the GLOBE/TACTICAL toggle and the
// old TacticalMap all collapsed into components/aircraftMap/ -- one map, local receiver + SWIM
// merged server-side (/api/map/aircraft). Kept as the route component so App.jsx and the CSS
// (.train-map-view shell) did not have to change shape.
import AircraftMap from './aircraftMap/AircraftMap.jsx'

export default function MapView({ liveState }) {
  return (
    <div className="train-map-view">
      <AircraftMap liveState={liveState} />
    </div>
  )
}
