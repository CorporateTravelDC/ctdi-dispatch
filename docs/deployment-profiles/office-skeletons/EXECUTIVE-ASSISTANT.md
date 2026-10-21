# Executive Assistant profile skeleton

> **Template, disabled until after the feature freeze** (staged 2026-10-20 UTC). Profile JSON:
> [`../profiles/executive-assistant.json`](../profiles/executive-assistant.json). Overview and rules:
> [README.md](README.md).

## Role

The Executive Assistant runs a principal's calendar, travel, correspondence and documents. Until 2026-10-20 this
was half of the combined `executive-assistant-chief-of-staff` profile. The operator split it because the EA and
the Chief of Staff are distinct executive-suite positions; the CoS half is now
[CHIEF-OF-STAFF.md](CHIEF-OF-STAFF.md).

- **Modules:** `calendar_coordination`, `meeting_preparation`, `travel_coordination`, `follow_up` and
  `document_intake`.
- **Sources:** `calendar`, `contacts`, `email` and `document_repository`.
- **Glossary scopes:** `scheduling` and `office`. `travel_coordination` keeps its existing `aviation`, `rail` and
  `weather` scopes, because the EA is the profile that touches dispatch. Rendered with
  `scripts/render-glossary.py --profile executive-assistant`.

## Approvals and data classes

- **Signed approvals:** `send_on_behalf_of_principal`, `book_travel` and `share_itinerary`.
- **PII classes:** `calendar`, `contacts`, `travel_itinerary` and `travel_documents`.

## Services (disabled)

| Service | Role in the profile |
|---|---|
| Nextcloud Calendar (with its Appointments booking), Contacts, Tasks | The principal's calendar (CalDAV), address book (CardDAV) and the follow-up list; installed with `--keep-disabled` |
| Paperless-ngx (shared with the CFO) | E-tickets, itineraries, receipts; tagged with the trip tag |

## Where it meets dispatch

A travel event that holds a flight number becomes a watchlist entry through the existing
`POST /api/v1/watchlist`, so the LADD filter, flight-alerts and the gate/baggage assignments
(`/api/v1/flights/{ident}/assignments`) apply unchanged. The event holds a flight number, never a tail: tails
come from SWIM only. Ground legs go to the runsheet and opsplan, and venue meetings to the EP advance. The
CalDAV adapter that would read the calendar is **not built**. It would be read-only, use an app password and be
disabled by default, and it needs its own operator decision after the freeze.
