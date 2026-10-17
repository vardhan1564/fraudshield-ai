# Spec 07 — Customer App (React web app, phone-sized PWA)

No native app. The customer app is part of `frontend-web`, routes under `/app/*`, designed for a phone screen.
It is how the demo shows the unique customer-side features: Scam Coach, call/screen-share signals,
trusted contact, payee reputation, report fraud, message scanner.

## 1. How it runs
- On a laptop: `/app` renders inside a centred phone frame (max-width 420 px) so a demo can show dashboard and phone side by side.
- On a real phone: open `http://<laptop-ip>:5173/app` on the same Wi-Fi (`npm run dev -- --host`). It fills the screen.
- Installable as a PWA (`vite-plugin-pwa`): name "FraudShield Pay", icon, standalone display.
- Device signals (on call, unknown caller, screen share, remote-access app) come from a **Simulate context** panel. Browsers can't read call state; this is stated in the PRD as a simulated signal.

## 2. Folder structure
```
frontend-web/src/customer/
├── layout/       PhoneFrame.tsx, BottomNav.tsx
├── pages/        Home, Pay, RiskWarning, ScamCoach, Approvals, Scanner, Report, Safety, Settings
├── components/   RiskBanner, CountdownTimer, YesNoQuestion, ReputationChip, ContextPanel
├── state/        contextStore.ts (zustand or React context: simulated device signals)
└── hooks/        usePayment, useCustomerSocket
```
Uses `shared/` for API client, types, i18n, RiskBadge, ReasonList.

## 3. Screens
| Route | Screen | Calls | Notes | Priority |
|---|---|---|---|---|
| `/app` | Home | `/transactions?mine=true` | fake balance, recent payments, safety tip | P0 |
| `/app/pay` | Pay | `/payees/{vpa}/reputation` on blur; `/transactions/score` on Pay | ReputationChip before paying; sends `context` from ContextPanel | P0 |
| `/app/pay/:txnId/warning` | Risk Warning | — | amber/red banner, reasons, explanation; big "Cancel", small "Continue" | P0 |
| `/app/pay/:txnId/coach` | Scam Coach | `/transactions/{id}/coach` | 2–3 yes/no questions, 10-min cooling-off timer, "Call my bank" (tel: link) | P0 |
| `/app/approvals` | Trusted Contact Approvals | `/approvals/pending`, `/approvals/{id}`, `/ws/customer` | contact approves/rejects a family member's payment | P1 |
| `/app/scan` | Scam Scanner | `/scan/message` | paste SMS/link; optional QR scan with `html5-qrcode` | P1 |
| `/app/report` | Report Fraud | `/reports` | one-tap report of payee/number | P0 |
| `/app/safety` | Safety Centre | `/trusted-contacts` | tips, flagged events, add trusted contact + limit | P1 |
| `/app/settings` | Settings | — | language (en/hi/mr), **Simulate context** panel | P0 |

## 4. UX rules (designed for older, first-time users)
- Minimum 16 px text, 44 px tap targets, high contrast, simple words, icons always with labels.
- Never show raw scores to customers. Show the explanation and reasons only.
- Red screens never auto-dismiss. "Cancel payment" is always the biggest button.
- Every screen available in English, Hindi, Marathi.

## 5. Acceptance
- **Digital-arrest demo:** in Settings turn on "On a call", "Unknown caller", "Screen sharing" → pay ₹48,500 to `refund.help@okbank` → Scam Coach appears → answer YES to the first question → red HOLD screen with "Call my bank". Within 1 s the dashboard shows the alert.
- **Normal payment:** ₹200 to a known payee → success screen in < 1 s.
- **Reported payee:** typing a VPA with ≥ 5 reports shows "Reported by N people" before paying.
- Lighthouse PWA check passes (installable).

## 6. Tests
- Vitest: YesNoQuestion, CountdownTimer, ContextPanel state.
- Playwright (mobile viewport, e.g. Pixel 7): digital-arrest flow and normal payment flow.
