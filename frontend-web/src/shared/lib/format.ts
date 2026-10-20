/** Formatting for Indian users (spec 06 §2, CLAUDE.md privacy rule 8). */

const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
  minimumFractionDigits: 0,
});

/** 48500 -> "₹48,500"; 125000.5 -> "₹1,25,000.5" (lakh grouping). */
export function formatInr(amount: number): string {
  return inr.format(amount);
}

const indianNumber = new Intl.NumberFormat("en-IN");

/** 1250000 -> "12,50,000". */
export function formatNumber(value: number): string {
  return indianNumber.format(value);
}

/** 0.0064 -> "0.64%". */
export function formatPercent(fraction: number, digits = 1): string {
  return `${(fraction * 100).toFixed(digits)}%`;
}

/** 10-digit mobile -> "98xxxxxx21" (first 2 and last 2 kept). Already-masked input is kept. */
export function maskPhone(phone: string | null | undefined): string {
  if (!phone) return "";
  if (/x/i.test(phone)) return phone; // the API already masks phones
  const digits = phone.replace(/\D/g, "").replace(/^(91|0)(?=\d{10}$)/, "");
  if (digits.length < 6) return "x".repeat(digits.length);
  return `${digits.slice(0, 2)}${"x".repeat(digits.length - 4)}${digits.slice(-2)}`;
}

/** "refund.help@okbank" -> "re***@okbank". */
export function maskVpa(vpa: string | null | undefined): string {
  if (!vpa) return "";
  const at = vpa.indexOf("@");
  if (at < 0) return `${vpa.slice(0, 2)}***`;
  return `${vpa.slice(0, Math.min(2, at))}***${vpa.slice(at)}`;
}

const istDateTime = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: true,
});

/** ISO timestamp -> "16 Oct, 02:14 am" in IST. */
export function formatDateTime(iso: string): string {
  return istDateTime.format(new Date(iso));
}

/** Seconds -> "4m 05s" (cooling-off timers, SLA). */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}
