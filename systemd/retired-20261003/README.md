# Retired 2026-10-03 -- duplicate Quadlets

These were stale second copies of units whose real, deployed source is
`.config/containers/systemd/` (the drift checker compares THAT tree against
`~/.config/containers/systemd/`). Nothing installed from here (install.sh
only copies `systemd/tmpfiles.d/`). They had drifted -- acarsrouter here still
carried the pre-2026-10-03 station-name override and the old station id --
so they were retired rather than re-synced, to leave one source of truth.
