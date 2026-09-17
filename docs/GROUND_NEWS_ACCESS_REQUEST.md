# Ground News Access Request — CTDI (Corporate Travel Dispatch Intelligence)

**Status:** Drafted, not yet sent. Fill in the bracketed fields below, send
the email in [Email template](#email-template) to Ground News, and record
the outcome (date, contact, decision) at the top of
[docs/DATA_SOURCES.md](DATA_SOURCES.md)'s Ground News entry once you hear
back — same convention this repo uses for every other access request (see
the FAA SWIM, NWWS-OI, EUROCONTROL, and JASDAT entries in that file).

This document exists so the request is asked openly, in writing, before any
code in this branch is pointed at a real Ground News endpoint — not asked
after the fact, and not skipped by treating a personal login as implicit
permission to automate against it.

---

## Why this document exists

CTDI is a self-hosted, single-operator dispatch intelligence platform (see
the main [README](../README.md)). It already integrates several
credentialed, high-trust data sources — FAA SWIM/NMS, NWWS-OI — using a
consistent model: **each deployment authenticates with its own operator's
own account**, runs entirely on hardware that operator controls, and never
shares, resells, proxies, or redistributes what it fetches.

Ground News doesn't currently publish a developer API. Third-party options
exist (paid scraping-as-a-service wrappers, unofficial scraper actors on
automation platforms) but all of them either resell/proxy access through a
vendor's own account or scrape without any agreement with Ground News at
all. Both were explicitly ruled out for this integration — not because they
wouldn't work technically, but because they don't fit CTDI's access model
and don't give Ground News any visibility into who's accessing what.

This request asks for the alternative: explicit, direct permission (or a
pointer to an existing sanctioned path, if one already exists that isn't
publicly documented) for an operator to authenticate their own,
individually-paid-for Ground News account from software they run
themselves, for their own personal/organizational use.

## What the integration actually does

- **One account per deployment.** Each self-hosted CTDI instance is
  configured with exactly one operator's own Ground News credentials (or a
  session token derived from their own already-authenticated browser
  session). There is no shared pool of accounts and no mechanism for one
  deployment to use another operator's credentials.
- **Reads only what that account can already see.** The integration
  fetches the operator's own personalized feed (their saved interests/
  topics) and public bias/coverage/Blindspot story listings — nothing
  behind another user's account, and nothing beyond whatever subscription
  tier the operator has personally paid for.
- **No redistribution.** Fetched items are stored locally (SQLite, on the
  operator's own hardware) and displayed back only to that same operator,
  merged into their own personal news-monitoring dashboard alongside
  public RSS feeds they've separately chosen to follow. Nothing is
  published, re-served to third parties, or made available to any other
  user of the software.
- **No resale.** CTDI itself is proprietary software used by its own
  operator (see the repository's [LICENSE](../README.md#license)) — this
  integration is not a product being sold that depends on Ground News
  data, and access is not being requested on behalf of a customer base.
- **Rate-conscious, single-account polling.** The fetcher polls at most
  once every 15 minutes (900 seconds) per deployment — see
  `src/poller/main.py`'s `FETCH_SCHEDULE` — the same interval used for
  this platform's other lower-frequency credentialed feeds (EUROCONTROL,
  JASDAT). It is not a high-frequency scrape.
- **Open about its auth model, not reverse-engineered.** The code in this
  branch (`src/common/ground_news_client.py`,
  `src/poller/fetchers/ground_news.py`) implements the storage, retry,
  credential-gating, and feed-merge plumbing, but deliberately leaves the
  actual authenticated request/response contract as a placeholder rather
  than guessing at Ground News's private frontend API. That piece gets
  filled in only after this request is answered — either against an
  endpoint Ground News confirms, or not at all if Ground News would rather
  this not happen.

## What we're asking for

One of, in order of preference:

1. Confirmation that an individual, paying Ground News subscriber may
   authenticate their own account from a personal script/self-hosted
   application for personal use, along with whatever technical contract
   (endpoint, auth header/cookie name, rate limits) Ground News is willing
   to document for that purpose.
2. Pointing us to an existing partner/API program (self-serve or reviewed)
   that covers this use case, if one already exists.
3. If neither is workable, a clear "no" — which is a complete and useful
   answer. In that case this branch stays unmerged and the feed stays
   permanently `awaiting_credentials`; see
   [docs/DATA_SOURCES.md](DATA_SOURCES.md)'s note against enabling this
   feed before sign-off.


## What happens after a response

- **Approved with a defined contract:** fill in
  `common/ground_news_client.py`'s `_login()` / `fetch_my_feed()` against
  the confirmed endpoint, update this document and
  `docs/DATA_SOURCES.md` with the real access process, remove the
  "pending sign-off" callouts, and merge the branch.
- **Pointed to an existing program:** same as above, adapted to that
  program's actual auth mechanism (which may replace the session-token /
  email-password modes here entirely, e.g. with a real API key).
- **Declined:** close this branch without merging the credentialed
  fetcher, or keep it merged-but-permanently-disabled with the
  `awaiting_credentials` gate as a documented "we asked, they said no"
  record — operator's call, but do not enable it either way.
