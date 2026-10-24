// src/runner/frontend/src/components/aircraftMap/altitudeColor.js -- marker FILL colour by altitude (2026-10-24).
//
// The NUMBERS below are tar1090's default `ColorByAlt` ramp (wiedehopf/tar1090, html/defaults.js,
// GPL-2.0), fetched from the public source on 2026-10-24 so this map reads exactly like the receiver's
// own tar1090 page: a dispatcher who learned "orange = low, green = 11k, purple = cruise" there sees the
// same thing here. Only the stop tables are copied; the interpolation is our own. tar1090 is credited here
// and in the legend.
//
// SOURCE (receiver / SWIM / matched) is never encoded in the fill -- see markers.js -- so altitude stays
// readable across every source style.

// alt (ft) -> hue (deg)
const HUE_STOPS = [
  { alt: 0,     val: 20 },
  { alt: 2000,  val: 32.5 },
  { alt: 4000,  val: 43 },
  { alt: 6000,  val: 54 },
  { alt: 8000,  val: 72 },
  { alt: 9000,  val: 85 },
  { alt: 11000, val: 140 },
  { alt: 40000, val: 300 },
  { alt: 51000, val: 360 },
]
const SATURATION = 88
// hue (deg) -> lightness (%), keeps the yellow/green band from washing out
const LIGHT_STOPS = [
  { h: 0,   val: 53 }, { h: 20,  val: 50 }, { h: 32,  val: 54 }, { h: 40,  val: 52 },
  { h: 46,  val: 51 }, { h: 50,  val: 46 }, { h: 60,  val: 43 }, { h: 80,  val: 41 },
  { h: 100, val: 41 }, { h: 120, val: 41 }, { h: 140, val: 41 }, { h: 160, val: 40 },
  { h: 180, val: 40 }, { h: 190, val: 44 }, { h: 198, val: 50 }, { h: 200, val: 58 },
  { h: 220, val: 58 }, { h: 240, val: 58 }, { h: 255, val: 55 }, { h: 266, val: 55 },
  { h: 270, val: 58 }, { h: 280, val: 58 }, { h: 290, val: 47 }, { h: 300, val: 43 },
  { h: 310, val: 48 }, { h: 320, val: 48 }, { h: 340, val: 52 }, { h: 360, val: 53 },
]
const UNKNOWN  = { h: 0,   s: 0, l: 75 }
const GROUND   = { h: 220, s: 0, l: 30 }
// tar1090 modifiers: stale = position older than its freshness threshold; selected = clicked aircraft.
const STALE_MOD    = { s: -35, l: 9 }
const SELECTED_MOD = { s: 10,  l: 5 }

function interp(stops, key, x) {
  if (x <= stops[0][key]) return stops[0].val
  const last = stops[stops.length - 1]
  if (x >= last[key]) return last.val
  for (let i = 1; i < stops.length; i++) {
    const a = stops[i - 1], b = stops[i]
    if (x <= b[key]) {
      const t = (x - a[key]) / (b[key] - a[key])
      return a.val + t * (b.val - a.val)
    }
  }
  return last.val
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))

/** HSL components for an altitude. `alt` is feet, the string 'ground', or null/undefined (unknown). */
export function altitudeHsl(alt, { stale = false, selected = false } = {}) {
  let c
  if (alt === 'ground') c = { ...GROUND }
  else if (alt == null || !Number.isFinite(Number(alt))) c = { ...UNKNOWN }
  else {
    const h = interp(HUE_STOPS, 'alt', Number(alt))
    c = { h, s: SATURATION, l: interp(LIGHT_STOPS, 'h', h) }
  }
  if (stale)    { c.s += STALE_MOD.s;    c.l += STALE_MOD.l }
  if (selected) { c.s += SELECTED_MOD.s; c.l += SELECTED_MOD.l }
  c.s = clamp(c.s, 0, 100)
  c.l = clamp(c.l, 0, 100)
  return c
}

/** CSS colour string for an altitude (the marker fill / trail segment colour). */
export function altitudeColor(alt, opts) {
  const { h, s, l } = altitudeHsl(alt, opts)
  return `hsl(${Math.round(h)}, ${Math.round(s)}%, ${Math.round(l)}%)`
}

/** Legend stops: label + colour, low to high, plus ground/unknown. */
export const LEGEND_ALTS = [0, 2000, 4000, 6000, 8000, 10000, 15000, 20000, 30000, 40000]
export const LEGEND_STOPS = LEGEND_ALTS.map(alt => ({
  alt,
  label: alt >= 1000 ? `${alt / 1000}k` : String(alt),
  color: altitudeColor(alt),
}))
export const GROUND_COLOR  = altitudeColor('ground')
export const UNKNOWN_COLOR = altitudeColor(null)

/** CSS linear-gradient() for the legend bar, sampled every 1000 ft to 40k. */
export function altitudeGradientCss(maxAlt = 40000) {
  const parts = []
  for (let a = 0; a <= maxAlt; a += 1000) {
    parts.push(`${altitudeColor(a)} ${(a / maxAlt * 100).toFixed(1)}%`)
  }
  return `linear-gradient(to right, ${parts.join(', ')})`
}

export const ALTITUDE_RAMP_CREDIT = 'Altitude colours: tar1090 default ramp (wiedehopf/tar1090)'
