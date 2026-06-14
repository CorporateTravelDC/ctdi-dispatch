import { useState } from 'react'

// MarineTraffic embed — centred on Chesapeake Bay / Potomac / DC area
// zoom:8 covers DC → Baltimore → upper Chesapeake in one view
const MT_EMBED_URL =
  'https://www.marinetraffic.com/en/ais/embed/' +
  'zoom:8/centery:38.9/centerx:-76.8/' +
  'maptype:0/shownames:true/mmsi:0/shipid:0/' +
  'fleet:/fleet_id:/vtypes:/showmenu:false/remember:false'

const MT_DIRECT_URL =
  'https://www.marinetraffic.com/en/ais/home/centerx:-76.8/centery:38.9/zoom:8'

export default function AisMapView() {
  const [err, setErr] = useState(false)

  return (
    <div className="train-map-view">
      {/* Sub-nav */}
      <div className="train-map-subnav">
        <span className="train-map-title">AIS</span>
        <span className="stat source-badge" style={{ color: 'var(--cyan)' }}>
          MarineTraffic — live vessel positions
        </span>
        <a
          href={MT_DIRECT_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="train-map-external"
          title="Open MarineTraffic in new tab"
        >↗</a>
      </div>

      {/* Map area */}
      <div className="globe-map-wrap">
        {err ? (
          <div className="globe-fallback">
            <p style={{ textAlign: 'center', padding: '0 1.5rem' }}>
              MarineTraffic blocked cross-origin embedding.
            </p>
            <a
              href={MT_DIRECT_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="globe-fallback-link"
            >
              Open MarineTraffic ↗
            </a>
          </div>
        ) : (
          <iframe
            src={MT_EMBED_URL}
            className="globe-iframe"
            title="MarineTraffic — AIS vessel positions"
            referrerPolicy="no-referrer"
            onError={() => setErr(true)}
            allow="fullscreen"
          />
        )}

        <div className="map-overlay-stats globe-stats">
          <span className="stat" style={{ color: 'var(--muted)', fontSize: '0.6rem' }}>
            Potomac River · Chesapeake Bay · Port of Baltimore
          </span>
        </div>
      </div>
    </div>
  )
}
