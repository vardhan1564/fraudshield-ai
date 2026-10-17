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

## 4. Acceptance
- Overview loads in < 1.5 s with seeded data.
- Live Monitor shows a new row within 1 s of a scored transaction.
- Case Detail: Confirm fraud → toast → alert leaves the queue (optimistic update).
- Graph Explorer renders 200 nodes smoothly; clicking a node loads its neighbourhood.
- Rules form rejects invalid conditions before submit.

## 5. Tests
- Vitest + React Testing Library: RiskBadge, ShapWaterfall, rules form.
- Playwright e2e: login as analyst → open first alert → confirm fraud.
