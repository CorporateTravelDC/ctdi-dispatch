import { useState, useEffect, useRef, useMemo } from 'react'

const REFRESH_MS = 60_000

function parseHex(notes) {
  if (!notes) return null
  const m = notes.match(/hex:\s*([0-9a-f]{6})/i)
  return m ? m[1].toLowerCase() : null
}

export function useWatchlist() {
  const [entries, setEntries] = useState([])
  const timerRef = useRef(null)

  async function fetchWatchlist() {
    try {
      const res = await fetch('/api/dispatch/api/v1/watchlist')
      if (!res.ok) return
      const data = await res.json()
      setEntries(data.entries ?? [])
    } catch (_) {}
  }

  useEffect(() => {
    fetchWatchlist()
    timerRef.current = setInterval(fetchWatchlist, REFRESH_MS)
    return () => clearInterval(timerRef.current)
  }, [])

  const { callsignSet, hexSet } = useMemo(() => {
    const cs = new Set()
    const hx = new Set()
    entries.forEach(e => {
      if (e.identifier) cs.add(e.identifier.toUpperCase().trim())
      const hex = parseHex(e.notes)
      if (hex) hx.add(hex)
    })
    return { callsignSet: cs, hexSet: hx }
  }, [entries])

  return { entries, callsignSet, hexSet }
}

export function callsignToIcao(callsign) {
  if (!callsign) return null
  const m = callsign.trim().toUpperCase().match(/^([A-Z]{2,3})[0-9]/)
  return m ? m[1] : null
}

export function airlineLogoUrl(callsign) {
  const icao = callsignToIcao(callsign)
  if (!icao) return null
  return 'https://www.flightaware.com/images/airline_logos/90p/' + icao + '.png'
}

export const FALLBACK_PLANE_SVG = "data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22%2300d4ff%22%3E%3Cpath%20d%3D%22M21%2016v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10%202.67%2010%203.5V9l-8%205v2l8-2.5V19l-2%201.5V22l3.5-1%203.5%201v-1.5L13%2019v-5.5l8%202.5z%22%2F%3E%3C%2Fsvg%3E"
