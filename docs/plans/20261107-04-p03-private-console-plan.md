# P03 — Private Console: Bounded Implementation Plan (not authorized)

**Status:** **APPROVED 2026-11-07 — stage 1 only.** The owner approved this plan and
answered the five blocking questions (§7): explicit UTC instant, **one configured
private path**, one new module, reconciliation rendered, labels confirmed. Approval
covers stage 1 (registry, loopback gate, Portfolio panel, configured path); stages 2-4
still stop for review before they begin. P02 acceptance authorized the CLI reports
only and did not authorize P03; this plan is the authorization for stage 1.

**Recorded:** 2026-11-07

**Inputs read for this plan:** specification §5.4/§9; ADR-0018 (private views and the
loopback boundary); ADR-0024 §11 and ADR-0026 (portfolio ledger, projections, marks);
ADR-0025 (immutable runs) and ADR-0027 (row-bound price origin, now implemented);
`assets/svg/read-api-and-p03-console-wireframe.svg`; the shipped
`dashboard/pages.py`, `dashboard/components.py`, `dashboard/streamlit_app.py`,
`dashboard/exporter.py`, `portfolio/{marks,projection,reconcile}.py`,
`signals/readiness.py`, `backtesting/evidence_view.py`.

## 1. What is being built

One loopback-only, **display-only** Streamlit surface that reads local evidence and
renders it honestly. It is not an API, not an exporter, not a trader, and not an
authenticated service — a loopback bind is not authentication (ADR-0018 §8).

Explicit non-goals: no new API routes; no ledger, marks or portfolio semantics change;
no writes of any kind from a page (no ledger events, no marks, no backfill, no
inference, no fitting); no static export, Pages, public API, W&B or log projection of
private state; no auth, multi-user or remote access; no P00-2/P00-4 accounting work; no
D01 source access.

## 2. Pages, in the required order (§5.4: Portfolio and Data readiness first)

Registered in `dashboard/pages.py` as additional gated `PageSpec`s (`streamlit=True`,
`static_file=None`, `gated=True`) so both the app and the static exporter consume one
vocabulary (`tests/unit/test_dashboard_ia_parity.py` keeps them aligned, and the
exporter must not learn about them at all).

| Order | Page | Data (all read-only) | Must refuse to show |
| --- | --- | --- | --- |
| 1 | **Portfolio** | `portfolio` replay at an explicit as-of; `value_positions`; `reconcile` | NAV/P&L/allocation when marks are missing, stale, conflicting or quantity-uncertain |
| 2 | **Data Readiness** | `signals/readiness.build_tracking_readiness`, monitoring health, per-table freshness | a market-union result presented as per-instrument coverage |
| 3 | **Research Runs** | `evidence_view.validated_runs` / `latest_success_by_slug` / `latest_attempt` | an unvalidated or legacy run presented as current evidence |
| 4 | **Signals** | established candidate read surface (watchlist + latest evaluated session) | any candidate shown as an order, a probability, or as "no signal" when unevaluated |

**Portfolio panel contents** (§5.4, minimum): as-of instant (explicit input, never the
clock), as-of NAV **or** an explicit `unavailable` with the valuation block reason,
cash, positions (quantity, basis status, mark age in completed exact-venue sessions,
`provenance` — the P02 `synthetic|unknown` mark flag, never the E01-6 price-origin
label), allocation/concentration only when the valuation is complete, and
reconciliation matched/mismatched/unavailable differences. Held and watch-only
instruments must be visually distinct from ledger positions and carry their own
"not a holding" label.

**Research Runs** now renders because E01 is complete: immutable run cards showing
protocol (`equity-run-manifest/4`), the derived `data_provenance` and its limits,
finalisation age, and the last attempt kept distinct from the last success. Legacy v3
runs remain readable and are labelled as such, never upgraded.

**Signals** are candidates only, each with reason and as-of timestamp, or `unavailable`
with the reason. Never an order, never a calibrated profit probability, never a
success state inferred from stale data.

## 3. State contract (every panel, every time)

Each panel renders exactly one state from the shipped closed vocabulary
(`components.Notice`): `INFO`, `SUCCESS`, `WARNING`, `ERROR`, `UNAVAILABLE`,
`PARTIAL`, `REFUSED`, `GATED`. Rules that the implementation must keep:

- `UNAVAILABLE` is never rendered as `0`, `—`, or an empty frame.
- `PARTIAL` renders the real subtotal and states that it is not a total.
- `REFUSED` is used when the loopback gate rejects the page (as private tracking
  already does) or an input is rejected; `GATED` when a planned-but-unauthorized panel
  is visible in navigation.
- Loading is an explicit state, not a blank page.
- All labels come from `pages.py`; all states go through `components.render_notice`.
  No new label, colour or CSS modifier is invented by P03.

## 4. Loopback boundary (fail closed, before any private read)

Reuse the shipped primitives — `streamlit_app.is_loopback_bind_address` (explicit IP
loopback or `localhost`, nothing else) and the `st.get_option("server.address")` read —
and apply them **before** importing or reading any private module, exactly as
`render_private_tracking_preview` already does. A non-loopback bind renders
`Notice.REFUSED` and returns; no ledger, watchlist, marks or statement data is touched,
and the failure message contains no private value. The ADR-0018 forwarded/proxy
rejection for the private API stays untouched.

## 5. Privacy boundary (tested, not asserted)

- Private data never reaches: the static exporter (`static_file=None`; the exporter
  iterates `STATIC_PAGES` only), the public API, W&B, or any log/error string.
- Pages open the ledger and marks **read-only**; a test asserts no write path is
  reachable from a render (no `commit`, no file creation, no lake read-modify-write).
- Dry-run/no-write proof: a page render over a temporary private directory leaves the
  directory byte-identical.
- The as-of instant and marks/statement paths are explicit inputs; nothing is
  discovered, defaulted to "now", fetched or backfilled.

## 6. Bounded file plan (four sequential stages, owner review per stage)

**Stage 1 — registry, gate, Portfolio.**
`dashboard/pages.py` (+4 gated specs), `dashboard/streamlit_app.py` (dispatch + a
reusable `require_loopback()` helper, keeping the existing tracking behaviour
byte-compatible), a new read-only projection module for the panel content (see open
question 3), `tests/unit/test_dashboard_p03_console.py` (states + loopback refusal
before read + no-write), `tests/unit/test_dashboard_ia_parity.py` extension.

**Stage 2 — Data Readiness.** Readiness/freshness panel with the market-union
disclosure; held/watch-only separation completed across both pages.

**Stage 3 — Research Runs and Signals.** Read-only run cards (protocol + derived
provenance + record age) and candidate-only signals with reasons/timestamps.

**Stage 4 — docs and final gates.** A P03 user guide section (states, inputs, privacy
limits, what it will not do), `ARCHITECTURE.md`, spec §10 entry, `docs/README.md`;
then focused → serial → parallel → ruff → mypy → catalog byte identity → `make
lint-md` → `git diff --check`, plus an independent adversarial reviewer per stage.

Not in scope anywhere: new Settings keys, dependencies, ADRs, API routes, migrations,
or any change to the ledger, marks, readiness or evidence contracts.

## 7. Owner answers (recorded 2026-11-07 — these are the binding decisions)

1. **As-of input:** require an **explicit UTC instant**; the clock is never used, and a
   non-UTC offset is refused rather than silently normalised.
2. **Marks and statement paths:** **one configured path** — `Settings.console.private_dir`
   (`EQUITY_CONSOLE__PRIVATE_DIR`) governs every private read, including the ledger at
   `<private_dir>/portfolio/portfolio.sqlite3`, plus `marks.json` and `statement.csv`
   inside it. Nothing is discovered or defaulted.
3. **Placement:** **one new module** — `dashboard/panels.py`.
4. **Reconciliation:** **rendered** in the Portfolio panel from the configured
   `statement.csv`; absent means "not requested", never an implied match.
5. **Page labels:** confirmed — `Portfolio`, `Data Readiness`, `Research Runs`, `Signals`.

## 8. Stop condition

Approval of this plan authorizes **stage 1 only**; each later stage stops for review
before the next begins. Nothing here authorizes D01, P00-2/P00-4, R00–R02, H00, a
public projection, deployment, or any commit beyond the reviewed stage.
