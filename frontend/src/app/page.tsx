"use client";

import { CheckCircle2, Lock, RotateCcw, ScanSearch, Wallet } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/components/auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Role } from "@/lib/types";

const STEPS = [
  {
    icon: Lock,
    title: "1. Fund: money is held, not taken",
    body: "The client pays each milestone with PayPal. PayPal authorizes and holds the funds; PactPay never holds money.",
  },
  {
    icon: ScanSearch,
    title: "2. Verify with evidence",
    body: "The freelancer submits a live site. An AI verifier checks every agreed criterion in a real browser and shows screenshots, measurements and reasoning.",
  },
  {
    icon: Wallet,
    title: "3. Release when it passes",
    body: "When the work passes and the client approves, PayPal captures the payment and pays the freelancer. If it fails, they see exactly what to fix.",
  },
];

export default function Home() {
  const { login } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function enter(role: Role) {
    setBusy(role);
    try {
      await login(role);
      router.push("/dashboard");
    } catch (error) {
      setMessage(`Could not reach the PactPay API: ${(error as Error).message}`);
      setBusy(null);
    }
  }

  async function resetDemo() {
    setBusy("reset");
    try {
      await api("/demo/reset", { method: "POST" });
      setMessage("Demo reset: a fresh, ready-to-fund bakery contract is waiting.");
    } catch (error) {
      setMessage(`Reset failed: ${(error as Error).message}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-12">
      <section className="grid gap-8 pt-6 md:grid-cols-[1.4fr_1fr] md:items-center">
        <div className="space-y-5">
          <p className="inline-flex rounded-full bg-emerald-50 px-3 py-1 text-sm font-medium text-emerald-700">
            AI-verified escrow for freelance work
          </p>
          <h1 className="text-4xl font-semibold tracking-tight text-slate-900 md:text-5xl">
            Freelancers get paid. Clients pay only for work that works.
          </h1>
          <p className="text-lg text-slate-600">
            PayPal holds the client&apos;s payment, an AI checks the delivered website against the agreed criteria with
            evidence, and the money is released the moment it passes. Humans can always override.
          </p>
          <p className="text-sm text-slate-500">
            <span className="font-medium text-slate-700">Why not Upwork?</span> Marketplaces rely on manual reviews and
            disputes. PactPay verifies the work automatically, with evidence, for any direct client-freelancer deal.
          </p>
        </div>

        <Card className="border-emerald-200 shadow-sm">
          <CardHeader>
            <CardTitle>Try the live demo</CardTitle>
            <CardDescription>PayPal sandbox: no real money moves. Switch roles any time from the header.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Button className="w-full" size="lg" disabled={busy !== null} onClick={() => enter("client")}>
              Continue as the client (Rosie&apos;s Bakery)
            </Button>
            <Button className="w-full" size="lg" variant="outline" disabled={busy !== null} onClick={() => enter("freelancer")}>
              Continue as the freelancer (Sam)
            </Button>
            <Button className="w-full" variant="ghost" disabled={busy !== null} onClick={resetDemo}>
              <RotateCcw className="mr-2 h-4 w-4" />
              {busy === "reset" ? "Resetting..." : "Reset demo"}
            </Button>
            {message && <p className="text-sm text-slate-600">{message}</p>}
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        {STEPS.map(({ icon: Icon, title, body }) => (
          <Card key={title}>
            <CardHeader>
              <Icon className="h-6 w-6 text-emerald-600" />
              <CardTitle className="text-base">{title}</CardTitle>
            </CardHeader>
            <CardContent className="text-sm text-slate-600">{body}</CardContent>
          </Card>
        ))}
      </section>

      <section className="rounded-xl border bg-white p-6">
        <h2 className="mb-3 font-semibold text-slate-900">Built to be trusted with money</h2>
        <ul className="grid gap-2 text-sm text-slate-600 md:grid-cols-2">
          {[
            "Only objectively checkable criteria; subjective ones go to the client",
            "Every verdict shows its evidence: screenshots, measurements, reasoning",
            "Low confidence or a manipulation attempt always goes to a human",
            "The AI can never move money: a strict state machine guards every PayPal call",
            "Idempotent capture and payout: a retry never pays twice",
            "Measured: 0 false passes and 0 false fails on a 49-case labeled test set",
          ].map((item) => (
            <li key={item} className="flex gap-2">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
              {item}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
