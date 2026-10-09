# CTDI parameterized deployment profiles — design pack v1

**Status: design-stage contracts, NOT directly executable CTDI deployment configuration.**
Adapters, migrations, policies, and runtime tests are required before deployment.

Profiles: chauffeur-regulated, executive-assistant-chief-of-staff, content-creator,
independent-journalist, cfo, cmo, cpa, gig-worker.

## Architecture
- Core: private networking, agent identity, human-signed approvals, audit, SBOM and provenance.
- Profile: objects, state machines, modules, data classifications, human approval points.
- Geography: explicit countries, regions, polygons/service areas, airports, stations,
  weather jurisdictions, local time zone, currency and units. No implicit DC coordinates.
- Sources: per-profile adapters, credentials by reference, licensing/entitlement checks.
- Host: architecture, Podman/Quadlets, Linux VM for Apple Silicon, GPU and RF capabilities.

## Production acceptance gates
1. Implement and test a typed profile loader and CTDI config adapter. Never silently ignore a key.
2. Refactor hardcoded geographic business logic behind a validated geography provider.
3. Enforce deny-by-default module, source and agent capability policies.
4. Require explicit source entitlement, licensing and jurisdictional review.
5. Use UTC internally, IANA timezone for display; test DST and region boundaries.
6. Treat absent receiver coordinates as configuration errors for distance-sensitive features.
7. Isolate sensitive journalist sources, CPA client data, financial records and passenger PII.
8. Prove all human approval gates are bound to real signed authorization mechanisms.
9. Run synthetic event replay and negative security tests in staging.
10. Preserve signed source, controlled build, artifact SBOM, receipt, runtime verification.
11. Never claim compliance certification from a template; obtain sector-specific review.
12. Pilot with a separate organization and credentials before production promotion.

`python3 validate_profiles.py` checks structure and security invariants.
These example profiles contain placeholders and must not be deployed unchanged.
