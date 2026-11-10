import { Banknote, Lock, RotateCcw, Send } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HELD_STATES, dateTime, money } from "@/lib/format";
import type { MilestoneOut } from "@/lib/types";

function Ref({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <p className="flex justify-between gap-4 text-xs text-slate-500">
      <span>{label}</span>
      <span className="truncate font-mono">{value}</span>
    </p>
  );
}

export function MoneyPanel({ m, perspective }: { m: MilestoneOut; perspective: "client" | "freelancer" }) {
  const held = HELD_STATES.includes(m.state) && m.state !== "releasing";
  let icon = <Banknote className="h-5 w-5 text-slate-500" />;
  let headline = `${money(m.amount, m.currency)} not funded yet`;
  let detail = "The client funds this milestone with PayPal before work starts.";

  if (held) {
    icon = <Lock className="h-5 w-5 text-indigo-600" />;
    headline = `${money(m.amount, m.currency)} held by PayPal, not taken`;
    detail =
      perspective === "freelancer"
        ? "The client's money is secured. It's released to you when the work passes."
        : "PayPal has authorized the payment. It's only captured when the work passes and you approve.";
  } else if (m.state === "releasing") {
    icon = <Send className="h-5 w-5 text-sky-600" />;
    headline = `${money(m.freelancer_receives, m.currency)} on its way to the freelancer`;
    detail = `Payment captured. PayPal payout status: ${m.payout_status ?? "sending"}.`;
  } else if (m.state === "released") {
    icon = <Send className="h-5 w-5 text-emerald-600" />;
    headline =
      perspective === "freelancer"
        ? `${money(m.freelancer_receives, m.currency)} paid to you`
        : `${money(m.freelancer_receives, m.currency)} paid to the freelancer`;
    detail = `Released ${dateTime(m.released_at)} via PayPal Payouts.`;
  } else if (m.state === "refunded") {
    icon = <RotateCcw className="h-5 w-5 text-slate-500" />;
    headline = "Hold released back to the client";
    detail = "The PayPal authorization was voided. Nothing was charged.";
  } else if (m.state === "cancelled") {
    headline = "Cancelled before funding";
    detail = "No money was held.";
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          {icon}
          {headline}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-slate-600">{detail}</p>
        <div className="space-y-1 rounded-md bg-slate-50 p-3 text-sm">
          <p className="flex justify-between">
            <span className="text-slate-500">Milestone amount</span>
            <span className="tabular-nums">{money(m.amount, m.currency)}</span>
          </p>
          <p className="flex justify-between">
            <span className="text-slate-500">PactPay fee</span>
            <span className="tabular-nums">-{money(m.platform_fee, m.currency)}</span>
          </p>
          <p className="flex justify-between font-medium">
            <span>Freelancer receives</span>
            <span className="tabular-nums">{money(m.freelancer_receives, m.currency)}</span>
          </p>
        </div>
        <div className="space-y-1">
          <Ref label="PayPal order" value={m.paypal_order_id} />
          <Ref label="Authorization" value={m.paypal_authorization_id} />
          <Ref label="Hold expires" value={m.authorization_expires_at ? dateTime(m.authorization_expires_at) : null} />
          <Ref label="Capture" value={m.paypal_capture_id} />
          <Ref label="Payout batch" value={m.payout_batch_id} />
        </div>
      </CardContent>
    </Card>
  );
}
