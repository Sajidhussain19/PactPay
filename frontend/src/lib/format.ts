import type { MilestoneState } from "./types";

export function money(value: string | number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(Number(value));
}

export function date(value: string | null | undefined): string {
  if (!value) return "-";
  return new Date(value).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return "-";
  return new Date(value).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type Tone = "neutral" | "info" | "warning" | "success" | "danger" | "money";

export const STATE_INFO: Record<MilestoneState, { label: string; tone: Tone; money: string }> = {
  draft: { label: "Draft", tone: "neutral", money: "Not funded" },
  agreed: { label: "Agreed", tone: "neutral", money: "Not funded yet" },
  funded: { label: "Funded", tone: "money", money: "Held by PayPal" },
  submitted: { label: "Submitted", tone: "info", money: "Held by PayPal" },
  verifying: { label: "Verifying", tone: "info", money: "Held by PayPal" },
  passed: { label: "Passed", tone: "success", money: "Held, ready to release" },
  failed: { label: "Changes needed", tone: "danger", money: "Held by PayPal" },
  needs_review: { label: "Needs review", tone: "warning", money: "Held by PayPal" },
  disputed: { label: "Disputed", tone: "warning", money: "Held by PayPal" },
  releasing: { label: "Releasing", tone: "info", money: "Paying the freelancer" },
  released: { label: "Paid", tone: "success", money: "Paid to freelancer" },
  refunded: { label: "Refunded", tone: "neutral", money: "Hold released to client" },
  cancelled: { label: "Cancelled", tone: "neutral", money: "Nothing was held" },
};

export const HELD_STATES: MilestoneState[] = [
  "funded",
  "submitted",
  "verifying",
  "passed",
  "failed",
  "needs_review",
  "disputed",
  "releasing",
];

export const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700 border-slate-200",
  info: "bg-sky-50 text-sky-700 border-sky-200",
  warning: "bg-amber-50 text-amber-800 border-amber-200",
  success: "bg-emerald-50 text-emerald-700 border-emerald-200",
  danger: "bg-rose-50 text-rose-700 border-rose-200",
  money: "bg-indigo-50 text-indigo-700 border-indigo-200",
};

export const CHECK_LABELS: Record<string, string> = {
  page_loads: "Page loads",
  element_present: "Content present",
  form_submits: "Form works",
  responsive_layout: "Mobile layout",
  performance: "Speed & size",
  visual_match: "Matches mockup",
  repo_check: "Repository",
  tests_pass: "Tests pass",
  manual: "Client confirms",
};
