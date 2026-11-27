# Platform-side sentinels

Status: added 2026-11-18 (operator). Code: `common/platform_sentinels.py` (shared plumbing) and one
module per sentinel; entry point `scripts/platform-sentinel.py`; units under `.config/systemd/user/`.
Migration: `pg_schema/0088_platform_sentinels.sql`.

## Why they exist

The 2026-11-04..11-17 lapse was on the agent side: a cloud-provider subscription lapsed, so the
agents that read the platform's data were offline. The FAA feeds kept flowing into the database the
whole time; what stopped was everything an agent would have noticed. On 2026-11-18, UA4006 (operated
as ASH4006, KSAV-KIAD) was airborne before its FDPS plan was visible here, and a fleet-wide check then
showed that 80-94 % of legs on every carrier have no stored pre-departure route version. That is a
standing gap, not a regression, and no agent had been looking.

So these checks run as user timers on the box. They need no agent, and they alert the operator
directly on `ops-health` (and by email where stated).

## The six

| # | sentinel | unit | when | alerts |
|---|---|---|---|---|
| 1 | per-watch plan guard | (poller, `common/fdps_predeparture.py`) | every poller tick | "FDPS plan not yet visible (TFMS has the schedule)" 45 min before departure; "airborne with no FDPS plan" once airborne |
| 2 | plan coverage | `corporatetraveldc-plan-coverage-sentinel.timer` | daily 04:40Z + up to 15 min, previous UTC day | an operator's coverage moving 20 points or more against its trailing 14-day median |
| 3 | ground-stop tripwire | `corporatetraveldc-ground-stop-tripwire.timer` | every 5 min | ground stops at 5 or more airports at once (off again at 3 or fewer); a GS or GDP at a watched flight's airport |
| 4 | per-centre first messages | `corporatetraveldc-artcc-sentinel.timer` | daily 05:10Z + up to 15 min, previous UTC day | a centre's first-message count down more than 60 % against its trailing median, or at zero |
| 5 | agent lapse | `corporatetraveldc-agent-lapse-sentinel.timer` | every 30 min | 6 h nudge, 12 h email, 24 h escalation; dismissal by SSH or the console. ChatGPT is excluded since 2026-11-27 (kept by #6) |
| 6 | cloud-agent link keeper | `corporatetraveldc-gateway-hold-sentinel.timer` | every 6 h | a week before a hold or refresh grant lapses; a watched connector with no hold (weekly); the Cowork daily check-in tripwire (30 h quiet) |

Every run is fail-soft: an error is logged with its reason, the run exits 0, and the next timer run
retries. The two daily reports skip a run while load1 is 24 or more. Times are UTC with ET beside
them. Reports: `/var/lib/corporatetraveldc/reports/<name>.json`; a Markdown copy of the daily
reports goes to `/srv/ctdc-handoff/platform-sentinels-from-corporatetraveldc/`, which the handoff
mirror carries into the vault for the cloud agents.

### 2. Plan coverage

`flight_events.first_seen_at`, `first_seen_centre` and `first_seen_source` are set when a row is
created and never updated. For each TFMS leg with a takeoff in the day, the report finds the
same-occurrence row (same callsign, origin and destination, first seen in the 20 h before takeoff)
and asks whether any FDPS message for it reached the box before OUT. Rows created before migration
0088 have no first-seen time and count as unknown, not as missing, so coverage becomes measurable
from the day the migration runs. A national-subscription cut-over should show here as a rise.

### 3. Ground-stop tripwire

Reads `nas_programs` GS and GDP rows. TFMS rows carry their own start and end times; NAS Status REST
rows carry only their latest sighting, so in a replay they count for 30 minutes after it.
`platform-sentinel.py ground-stop --replay <from> <to>` prints what the rule would have fired, with no
push. The 2026-11-12T12:00Z..11-14T12:00Z replay (the 11-13 ground stops, inside the agent lapse)
finds two wide episodes at the default of 5 airports: 2026-11-12 18:45Z (13:45 ET) to 11-13 00:15Z
(19:15 ET), the New York area plus Boston and Philadelphia; and 11-14 02:10Z (11-13 21:10 ET) to
04:20Z (23:20 ET), Newark, Minneapolis, Chicago O'Hare, San Diego and Seattle. Without the hysteresis
the same window flapped on and off every few minutes.

### 4. Per-centre first messages

Counts, per centre, the flights whose first FDPS message to reach the box came from it, how many of
those first messages were filed plans, and how many arrived before the flight's OUT. It also tests
the 2026-11-18 hypothesis that our FDPS subscription is filtered by centre: a centre that never
delivers a pre-departure first message over 14 days, while others do, is listed as such.

### 5. Agent lapse

Accounts: active board signers of kind `agent` plus the registry (`/etc/ctdc-accounts.conf`), less
the service identities (`AGENT_LAPSE_EXCLUDE`, default `ctdc-agent-dispatch,ctdc-agent-llama`).
Activity: the account's last board post, last connector tool call (`agent.tool.call` audit rows) and
last signed on-box request (`board.signed.request` audit rows).

| silence | action |
|---|---|
| 6 h (`AGENT_LAPSE_NUDGE_H`) | ntfy priority 2 + a board coord post from `dispatch` to the account |
| 12 h (`AGENT_LAPSE_EMAIL_H`) | ntfy priority 3 with email to the operator (ntfy's SMTP relay, the same path `ntfy_push.send(email=True)` uses everywhere) |
| 24 h (`AGENT_LAPSE_ESCALATE_H`) | priority 4 + email + board post, with each kind of last activity, liveness states (inert marker, kill orders, signer active, tokens revoked in 24 h), whether every account of one vendor is silent together (read as a cloud-provider subscription or login lapse), the commands below, and an "Open console (dismiss)" button |

Each stage is sent once per silence episode; activity ends the episode. An inert account is listed
(stage `inert`) but not escalated, because the liveness switch has already turned it off
(`AGENT_LAPSE_ESCALATE_INERT=1` escalates it anyway).

Commands the escalation names:

    sudo /usr/local/libexec/ctdc/team-liveness.sh --status --user ACCOUNT
    sudo /usr/local/libexec/ctdc/team-liveness.sh --reactivate ACCOUNT --i-am-the-operator
    bash scripts/relink/ACCOUNT.sh            (when that relink script exists)
    scripts/ctdc-agent-lapse.sh dismiss --account ACCOUNT --for 24h --reason "why"

**Dismissal** holds until its window ends (default 24 h, at most 30 days) or the account is active
again, and is recorded in `agent_lapse_dismissals` (who, when, via, reason) plus an audit row.

- SSH: `scripts/ctdc-agent-lapse.sh dismiss ...` runs only as the operator account.
- ntfy and console: the escalation push's button opens `/console?lapse=ACCOUNT#agent-lapse`. A tap
  decides nothing. The console needs its normal sign-in, a human SSH-signed `console-login`
  approval, and the dismiss form is CSRF-checked inside that session. This is the path for a held
  or stale account during a cloud-provider lapse.

## The cloud-agent link keeper (2026-11-27)

Cloud agents reach the platform only through their gateway link. `common/gateway_hold.py` watches the
connectors in `GATEWAY_HOLD_SLUGS` (default `cowork,chatgpt`) and reminds the operator, once per target, as an
ops-health push and a board coord post:

| kind | when | what to run |
|---|---|---|
| `gateway_hold_ending` | the hold ends within 7 days | `scripts/agent-gateway.sh hold SLUG --days 90` (your signature) |
| `gateway_relink_due` | no live hold, and the refresh grant idles out within 7 days | the same hold, or a re-link after it lapses |
| `gateway_hold_missing` | no hold at all (weekly), or the link is revoked / never made | hold it; a revoked link needs a re-link from the vendor's settings |
| `gateway_link_quiet` | the tripwire: no call or renewal for `GATEWAY_LINK_QUIET` hours (`cowork=30`, `chatgpt=0` = off) | check the connector in the vendor's settings; every scheduled task should end with `workspace_contribute` |

Cowork runs scheduled tasks every day, so its daily call is its check-in. ChatGPT is used now and then, so it has
no quiet tripwire: its 90-day hold keeps it linked, since 2026-11-27 a hold lasts its full term (a renewal no longer
clears it), and the reminder comes a week before it ends. `platform-sentinel.py gateway-hold status` shows each link.

## Not verified live (2026-11-18)

The units have not run on the box; the first run is the relay's last step. Real ntfy delivery and
the email relay were not exercised by tests. The board nudge goes to a cloud agent only when it next
reads the board. Login-refresh age is root-only, so the escalation points to
`team-liveness.sh --status` for it instead of reporting it.
