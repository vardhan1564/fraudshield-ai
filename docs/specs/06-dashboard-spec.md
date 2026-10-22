# Spec 06 — Analyst / Admin Dashboard (React, desktop)

Lives in the same React project as the customer app (spec 07). Routes under `/dashboard/*`.

## 1. Shared project structure (both front ends)
```
frontend-web/src/
├── shared/
│   ├── api/          client.ts (axios + JWT refresh), endpoints/*.ts, ws.ts (auto-reconnect)
│   ├── types/        api.ts (mirrors docs/specs/04 exactly)
│   ├── components/   ui/ (shadcn), RiskBadge, ReasonList, Money, MaskedText, EmptyState, ErrorState
│   ├── hooks/        useAuth, useWebSocket, ...
│   ├── lib/          format.ts (₹, masking), riskColor.ts
│   └── i18n/         en.json, hi.json, mr.json (react-i18next)
├── dashboard/        layout/, pages/, components/ (KpiCard, ShapWaterfall, GraphView, DataTable)
├── customer/         see spec 07
├── App.tsx, main.tsx, routes.tsx   (role-based redirect after login)
```
Login is shared at `/login`: analysts/admins go to `/dashboard`, customers go to `/app`.

_(T4.1)_ As built: Vite 8 + React 18.3 + TypeScript 5.9 (strict, `noUncheckedIndexedAccess`) + Tailwind 4 + shadcn/ui-style components (`shared/components/ui/`) + TanStack Query 5 + React Router 7 + react-i18next (keys type-checked against `en.json`) + MSW 2. `api/client.ts` (axios) adds the bearer token and, on a 401, refreshes once and retries; parallel 401s share **one** refresh (refresh tokens rotate). Access token in memory, refresh token in localStorage (HttpOnly cookie = T6.2 follow-up). `api/ws.ts` reconnects with back-off 1→30 s and refreshes first after a 1008 close. `npm run dev:mock` runs the whole UI on MSW mocks of the P0 endpoints + WebSockets (`src/mocks/`, demo logins on the login page); `npm test` uses the same mocks. Design tokens (OKLCH, light/dark) in `src/index.css`; Inter + Noto Sans Devanagari. The dashboard is responsive (sidebar becomes a drawer below 1024 px) although spec §2 targets ≥ 1280 px.

## 2. Design rules
- Risk colours from `shared/lib/riskColor.ts`: green < 0.30, amber 0.30–0.70, red > 0.70. Always pair colour with a text label.
- Currency `₹48,500` (en-IN grouping). Mask `98xxxxxx21`, VPA `re***@okbank`.
- Light and dark theme. Desktop layout, min width 1280 px.
- Every list has loading skeleton, empty state, error state.

## 3. Pages
| Route | Page | Role | Data | Key components | Priority |
|---|---|---|---|---|---|
| `/dashboard` | Overview | analyst, admin | `/metrics/overview` | 5 KpiCards; fraud-by-hour line; fraud-by-type bar; city table | P0 |
| `/dashboard/live` | Live Monitor | analyst | `/ws/alerts` + `/transactions` | streaming table (last 200), filters, pause | P0 |
| `/dashboard/alerts` | Alert Queue | analyst | `/alerts`, WS | sortable table, assign-to-me, SLA timer | P0 |
| `/dashboard/cases/:id` | Case Detail | analyst | `/cases/{id}`, `/transactions/{id}/explain` | txn card, ShapWaterfall, explanation (live update), user history, device, actions: Confirm fraud / Mark legit / Escalate, notes | P0 |
| `/dashboard/graph/:entityId?` | Graph Explorer | analyst | `/graph/entity/{id}`, `/graph/rings` | Cytoscape view, node colour = risk, ring highlight, side panel | P0 |
| `/dashboard/payees` | Payee Reputation | analyst | `/payees/{vpa}/reputation` | search, report list | P1 |
| `/dashboard/rules` | Rules Manager | admin | `/rules` | table + form builder (field/op/value), JSON preview | P1 |
| `/dashboard/models` | Model Monitor | admin | `/models` | versions, metrics, drift status, Deploy | P1 |
| `/dashboard/audit` | Audit Log | admin | `/audit-log` | filters, CSV export | P1 |

_(T4.2)_ Overview as built: range presets 24 h / 7 d / 30 d in one row above everything; 5 KPI tiles (payments checked, fraud caught + flagged %, money protected in lakh/crore with the full ₹ value on hover, false-positive rate or "No labelled cases yet", p95 with an icon + words "Within/Over the 100 ms budget"); activity by IST hour as **two small charts on one hour axis** (volume, flagged; never a dual-axis chart, flagged is ~2% of volume) with a synced crosshair; fraud by type as horizontal bars (largest first, value at the tip, total below); top-10 city table with share bars. Every chart has a Chart/Table toggle. Chart colours are their own tokens (`--chart-volume` blue, `--chart-flagged` teal), validated for colour-blind and normal-vision separation in light and dark. Loading = same-shape skeletons, empty = "No payments in this period yet", error = message + Try again; refresh every 30 s keeps the old frame faded. Measured on 109,416 payments (production build, real API, Power saver laptop): full reload to KPIs + charts p50 ~1.0 s, max 1.09 s.

_(T4.3)_ Live Monitor as built: HOLDs arrive over `/ws/alerts` (`alert.created`, then `GET /transactions/{txn_id}` for the row, with an "Open case" link); every other payment comes from polling `/transactions` every 3 s, because spec 04 §3 pushes nothing for APPROVE/STEP_UP. Newest first, capped at 200 rows; filters by decision and channel; Pause freezes the list and counts what arrived meanwhile; a Live / Reconnecting status chip (the socket reconnects with jittered back-off). Measured in Chrome on the real API + worker: a scored HOLD is on screen in 195–480 ms (8/8 runs; alert.created alone 50–222 ms over 20 runs). **Acceptance note:** §4 says "a new row within 1 s of a scored transaction"; that holds for HOLDs (the plan's T4.3 done-when). APPROVE/STEP_UP rows can take up to ~3 s. Getting them under 1 s would need a backend push of every scored payment to `ws:analysts`; proposed edit to §4: "HOLD rows within 1 s; other rows within the 3 s poll".
Alert Queue as built: open + assigned alerts from `/alerts`, refetched when the socket reports a change (30 s poll as a backup); tabs All open / Unassigned / Assigned to me; sortable by severity (default, then oldest), amount, risk, waiting and SLA; main reason = first `top_reasons` entry; Assign to me is optimistic (rolls back on error); SLA countdown per alert: high 15 min, medium 1 h, low 4 h, "soon" in the last quarter, then overdue.

_(T4.4)_ Case Detail as built: two columns on desktop (one on phones). Left: payment card (amount, masked payee, risk + decision, customer name + masked phone, channel, category, action, payment ID, model); "Why it was held" = the explanation sentence (from `/transactions/{id}/explain?lang=`, replaced live by `explanation.ready` on `/ws/alerts` with an "Updated just now" badge) + the case's `top_reasons`; **ShapWaterfall** = the 10 biggest SHAP pushes by |impact| as a waterfall (each bar starts where the previous ended, then the net effect), red `--chart-raises` / blue `--chart-lowers` (a diverging pair validated with the dataviz checks: CVD ΔE 21.6 light / 19.2 dark), legend in words, signed value at each bar end, Chart/Table toggle (table adds the running total). Right: Decision card (optional note; Confirm fraud = `label fraud + status closed`, Mark legit = `label legit + status closed`, Escalate = `severity high + status open + unassigned`, needs a note, spec 04 §2.4), all optimistic on the case and the alert queue cache (a closed alert leaves the queue at once), success/error toast, rollback on error; device and context card (`GET /transactions/{id}`); the customer's last 10 payments (`GET /transactions?user_id=`), this one highlighted. Shared `ToastProvider` + `useToast` (no new library). Playwright e2e `e2e/case-detail.spec.ts` (login as analyst -> first alert -> confirm fraud -> gone from the queue) runs on the mocks by default and on the real stack with `E2E_BASE_URL`.

## 4. Acceptance
- Overview loads in < 1.5 s with seeded data.
- Live Monitor shows a new row within 1 s of a scored transaction.
- Case Detail: Confirm fraud → toast → alert leaves the queue (optimistic update).
- Graph Explorer renders 200 nodes smoothly; clicking a node loads its neighbourhood.
- Rules form rejects invalid conditions before submit.

## 5. Tests
- Vitest + React Testing Library: RiskBadge, ShapWaterfall, rules form. _(RiskBadge T4.3, ShapWaterfall T4.4 done)_
- Playwright e2e: login as analyst → open first alert → confirm fraud. _(T4.4: `npx playwright test`)_
