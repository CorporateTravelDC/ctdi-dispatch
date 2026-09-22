-- Migration 0058: Amtrak station coordinate reference table.
--
-- Geometric reasoning Phase 0 (docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md,
-- decision #5): "in-repo static dataset, cifp_*-style -- owned by the
-- operator or a future self-hosted operator of this platform." Small,
-- static, no AIRAC-style refresh cycle needed (station locations don't
-- move), so seeded directly in this migration rather than via a separate
-- loader script the way CIFP fix/procedure data is (0052_reference_cifp.sql).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, INSERT ... ON CONFLICT DO NOTHING.
--
-- PARTIAL SEED, NOT THE FULL ~30-STATION NEC LIST THE DESIGN DOC SCOPED:
-- station_code is cross-checked against src/poller/fetchers/amtrak.py's
-- regional_stations() default set (WAS/BWI/NCR/ALX/BAL/ABE/WIL/NPN) plus
-- a handful of other major, high-confidence Northeast Corridor stations
-- (NYP/PHL/BOS/BBY/PVD/NHV/STM/TRE/MET/NWK). Coordinates below are from
-- general public knowledge of these stations' real locations, NOT pulled
-- from a verified machine-readable source (no Amtrak GTFS/station API
-- feed was consulted -- none is wired into this platform today). Every
-- row here is a major, unambiguous, high-confidence station; minor/branch
-- stops were deliberately left out rather than guessed. Before relying on
-- this table for anything precision-sensitive, or before expanding it
-- toward the full ~30, re-verify against Amtrak's own published station
-- list or a GTFS feed -- don't grow this file by the same
-- general-knowledge method that seeded it.
CREATE TABLE IF NOT EXISTS station_coordinates (
    station_code    TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    source          TEXT NOT NULL DEFAULT 'general_knowledge_unverified'
);

INSERT INTO station_coordinates (station_code, name, lat, lon) VALUES
    ('WAS', 'Washington Union Station',   38.8973, -77.0063),
    ('BWI', 'BWI Rail Station',           39.1949, -76.6900),
    ('NCR', 'New Carrollton',             38.9469, -76.8716),
    ('ALX', 'Alexandria',                 38.8068, -77.0581),
    ('BAL', 'Baltimore Penn Station',     39.3079, -76.6156),
    ('ABE', 'Aberdeen, MD',               39.5087, -76.1641),
    ('WIL', 'Wilmington, DE',             39.7367, -75.5497),
    ('NPN', 'Newport News, VA',           37.0840, -76.4730),
    ('PHL', 'Philadelphia 30th Street',   39.9566, -75.1819),
    ('TRE', 'Trenton, NJ',                40.2206, -74.7563),
    ('MET', 'Metropark, NJ',              40.5717, -74.3616),
    ('NWK', 'Newark Penn Station',        40.7342, -74.1645),
    ('NYP', 'New York Penn Station',      40.7506, -73.9935),
    ('STM', 'Stamford, CT',               41.0468, -73.5423),
    ('NHV', 'New Haven Union Station',    41.2987, -72.9257),
    ('PVD', 'Providence, RI',             41.8296, -71.4144),
    ('BBY', 'Boston Back Bay',            42.3474, -71.0757),
    ('BOS', 'Boston South Station',       42.3519, -71.0552)
ON CONFLICT (station_code) DO NOTHING;
