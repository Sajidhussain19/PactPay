"use client";

import { ArrowLeft, CheckCircle2, Circle } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth";
import { RequireLogin } from "@/components/require-login";
import { StateBadge } from "@/components/state-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { CHECK_LABELS, date, dateTime, money } from "@/lib/format";
import type { ContractOut } from "@/lib/types";

export default function ContractPage() {
  return (
    <RequireLogin>
      <ContractDetail />
    </RequireLogin>
  );
}

function Approval({ who, at }: { who: string; at: string | null }) {
  return (
    <p className="flex items-center gap-2 text-sm">
      {at ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <Circle className="h-4 w-4 text-slate-300" />}
      <span className="text-slate-700">{who}</span>
      <span className="text-slate-400">{at ? `approved ${dateTime(at)}` : "not approved yet"}</span>
    </p>
  );
}

function ContractDetail() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const [contract, setContract] = useState<ContractOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<ContractOut>(`/contracts/${id}`)
      .then(setContract)
      .catch((e: Error) => setError(e.message));
  }, [id]);

  useEffect(load, [load, user?.id]);

  async function approve() {
    setBusy(true);
    try {
      setContract(await api<ContractOut>(`/contracts/${id}/approve`, { method: "POST" }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !contract) return <p className="text-rose-600">{error}</p>;
  if (!contract || !user) return <p className="text-slate-500">Loading...</p>;

  const myApproval = user.id === contract.client.id ? contract.client_approved_at : contract.freelancer_approved_at;
  const canApprove = contract.status === "draft" && !myApproval && user.role !== "admin";

  return (
    <div className="space-y-6">
      <Link href="/dashboard" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800">
        <ArrowLeft className="h-4 w-4" /> Dashboard
      </Link>
      <div>
        <p className="text-sm text-slate-500">
          {money(contract.total, contract.currency)} · created {date(contract.created_at)}
        </p>
        <h1 className="text-2xl font-semibold text-slate-900">{contract.title}</h1>
        <p className="text-slate-600">{contract.summary}</p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Card className={canApprove ? "border-amber-200" : undefined}>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {contract.status === "draft" ? "Both parties approve the criteria before funding" : "Agreed by both parties"}
          </CardTitle>
          <CardDescription>
            The criteria below are exactly what the AI verifier will check. Payment is released only when they pass.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Approval who={`${contract.client.name} (client)`} at={contract.client_approved_at} />
          <Approval who={`${contract.freelancer?.name ?? "Freelancer"} (freelancer)`} at={contract.freelancer_approved_at} />
          {canApprove && (
            <Button disabled={busy} onClick={approve}>
              Approve these criteria
            </Button>
          )}
        </CardContent>
      </Card>

      {contract.warnings.length > 0 && (
        <Alert>
          <AlertDescription>
            <p className="font-medium">Adjusted by PactPay when drafting:</p>
            <ul className="list-disc pl-5">
              {contract.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      {contract.milestones.map((m) => (
        <Card key={m.id}>
          <CardHeader className="flex flex-row items-start justify-between gap-4 pb-3">
            <div>
              <CardTitle className="text-base">
                {m.code} · {m.title}
              </CardTitle>
              <CardDescription>
                {money(m.amount, m.currency)} · due {date(m.due_date)}
              </CardDescription>
            </div>
            <div className="flex items-center gap-3">
              <StateBadge state={m.state} />
              <Link href={`/milestones/${m.id}`} className={buttonVariants({ size: "sm", variant: "outline" })}>
                Open
              </Link>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {m.criteria.map((c) => (
              <div key={c.code} className="flex gap-3 text-sm">
                <span className="w-14 shrink-0 text-xs text-slate-400">{c.code}</span>
                <div>
                  <p className="text-slate-800">{c.description}</p>
                  <p className="text-xs text-slate-500">
                    {CHECK_LABELS[c.check_type]}
                    {c.is_objective ? " · checked automatically" : " · confirmed by the client"}
                  </p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
