"use client";

import { FileText, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth";
import { MilestoneGrid } from "@/components/milestone-grid";
import { RequireLogin } from "@/components/require-login";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { HELD_STATES, money } from "@/lib/format";
import type { ContractOut, MilestoneOut } from "@/lib/types";

function Kpi({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{value}</CardTitle>
      </CardHeader>
      <CardContent className="text-xs text-slate-500">{hint}</CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  return (
    <RequireLogin>
      <Dashboard />
    </RequireLogin>
  );
}

function Dashboard() {
  const { user } = useAuth();
  const [contracts, setContracts] = useState<ContractOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<ContractOut[]>("/contracts")
      .then(setContracts)
      .catch((e: Error) => setError(e.message));
  }, [user?.id]);

  if (error) return <p className="text-rose-600">{error}</p>;
  if (!contracts || !user) return <p className="text-slate-500">Loading...</p>;

  const milestones: MilestoneOut[] = contracts.flatMap((c) => c.milestones);
  const sum = (items: MilestoneOut[], field: "amount" | "freelancer_receives" = "amount") =>
    items.reduce((total, m) => total + Number(m[field]), 0);
  const held = milestones.filter((m) => HELD_STATES.includes(m.state));
  const released = milestones.filter((m) => m.state === "released");
  const waitingOnMe = milestones.filter((m) =>
    m.allowed_actions.some((a) => ["fund", "approve_release", "review_pass", "submit"].includes(a)),
  );
  const isFreelancer = user.role === "freelancer";
  const pendingApproval = contracts.filter(
    (c) => c.status === "draft" && (isFreelancer ? !c.freelancer_approved_at : !c.client_approved_at),
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">
            {isFreelancer ? "Your work and payments" : "Your contracts"}
          </h1>
          <p className="text-slate-500">Signed in as {user.name}</p>
        </div>
        {user.role === "client" && (
          <Link href="/contracts/new" className={buttonVariants()}>
            <Plus className="mr-2 h-4 w-4" />
            New contract
          </Link>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <Kpi label="Held by PayPal" value={money(sum(held))} hint={`${held.length} milestone(s); authorized, not captured`} />
        <Kpi
          label={isFreelancer ? "Paid to you" : "Released"}
          value={money(isFreelancer ? sum(released, "freelancer_receives") : sum(released))}
          hint={`${released.length} milestone(s) paid out`}
        />
        <Kpi label="Active milestones" value={String(milestones.filter((m) => !["released", "refunded", "cancelled"].includes(m.state)).length)} hint="Not yet paid, refunded or cancelled" />
        <Kpi label="Waiting on you" value={String(waitingOnMe.length + pendingApproval.length)} hint="Fund, submit, review, approve" />
      </div>

      {pendingApproval.length > 0 && (
        <Card className="border-amber-200 bg-amber-50/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Contracts waiting for your approval</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {pendingApproval.map((c) => (
              <Link key={c.id} href={`/contracts/${c.id}`} className="flex items-center gap-2 text-sm text-slate-700 hover:underline">
                <FileText className="h-4 w-4" />
                {c.title} · {money(c.total, c.currency)}
              </Link>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Milestones</CardTitle>
          <CardDescription>Click a row to fund, deliver, review the AI verification, or release payment.</CardDescription>
        </CardHeader>
        <CardContent>
          <MilestoneGrid contracts={contracts} viewer={user.role} />
        </CardContent>
      </Card>
    </div>
  );
}
