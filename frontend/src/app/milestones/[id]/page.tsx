"use client";

import { ArrowLeft, CreditCard, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth";
import { MoneyPanel } from "@/components/money-panel";
import { ReportView } from "@/components/report-view";
import { RequireLogin } from "@/components/require-login";
import { StateBadge } from "@/components/state-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { VerificationTimeline } from "@/components/verification-timeline";
import { DEMO_SITES, api, demoSiteUrl } from "@/lib/api";
import { CHECK_LABELS, date } from "@/lib/format";
import type { MilestoneOut } from "@/lib/types";

export default function MilestonePage() {
  return (
    <RequireLogin>
      <Suspense fallback={<p className="text-slate-500">Loading...</p>}>
        <Milestone />
      </Suspense>
    </RequireLogin>
  );
}

function Milestone() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const { user } = useAuth();
  const [m, setM] = useState<MilestoneOut | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(
    search.get("funded") ? "Funded: PayPal is now holding the payment." : null,
  );

  const load = useCallback(async () => {
    try {
      setM(await api<MilestoneOut>(`/milestones/${id}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load, user?.id]);

  // Payouts settle asynchronously; poll while money is moving.
  useEffect(() => {
    if (m?.state !== "releasing") return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [m?.state, load]);

  async function act(label: string, path: string, json?: unknown) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      const result = await api<MilestoneOut | { milestone: MilestoneOut; approve_url?: string | null }>(path, {
        method: "POST",
        json: json ?? {},
      });
      if ("approve_url" in result && result.approve_url) {
        window.location.href = result.approve_url; // PayPal checkout, returns via /paypal/return
        return;
      }
      setM("milestone" in result ? result.milestone : result);
    } catch (e) {
      setError((e as Error).message);
      await load();
    } finally {
      setBusy(null);
    }
  }

  if (error && !m) return <p className="text-rose-600">{error}</p>;
  if (!m || !user) return <p className="text-slate-500">Loading...</p>;

  const perspective = user.role === "freelancer" ? "freelancer" : "client";
  const can = (action: string) => m.allowed_actions.includes(action);
  const report = m.latest_run?.report ?? null;
  const verifying = m.state === "verifying" || m.latest_run?.status === "running";

  return (
    <div className="space-y-6">
      <Link href="/dashboard" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800">
        <ArrowLeft className="h-4 w-4" /> Dashboard
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm text-slate-500">
            <Link href={`/contracts/${m.contract_id}`} className="hover:underline">
              Contract
            </Link>{" "}
            · {m.code} · due {date(m.due_date)}
          </p>
          <h1 className="text-2xl font-semibold text-slate-900">{m.title}</h1>
        </div>
        <StateBadge state={m.state} className="px-3 py-1 text-sm" />
      </div>

      {notice && (
        <Alert className="border-emerald-200 bg-emerald-50">
          <AlertDescription className="text-emerald-800">{notice}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          {/* ---------------------------------------------------------- actions */}
          {can("fund") && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Fund this milestone</CardTitle>
                <CardDescription>
                  PayPal authorizes and holds the payment. Nothing is taken until the work passes and you approve.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-3">
                <Button disabled={busy !== null} onClick={() => act("fund", `/milestones/${id}/fund`, { method: "paypal" })}>
                  {busy === "fund" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  Pay with PayPal
                </Button>
                <Button
                  variant="outline"
                  disabled={busy !== null}
                  onClick={() => act("card", `/milestones/${id}/fund`, { method: "sandbox_card" })}
                >
                  {busy === "card" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CreditCard className="mr-2 h-4 w-4" />}
                  Instant demo funding (sandbox test card)
                </Button>
              </CardContent>
            </Card>
          )}

          {can("submit") && <SubmitForm busy={busy} onSubmit={(body) => act("submit", `/milestones/${id}/submissions`, body)} />}

          {can("start_verification") && !verifying && (
            <Button variant="outline" disabled={busy !== null} onClick={() => act("verify", `/milestones/${id}/verify`)}>
              <RefreshCw className="mr-2 h-4 w-4" /> Run verification again
            </Button>
          )}

          {(can("approve_release") || can("review_pass") || can("dispute") || can("retry_release")) && (
            <Decisions m={m} busy={busy} act={act} />
          )}

          {(can("cancel") || can("refund")) && (
            <Button
              variant="ghost"
              size="sm"
              className="text-slate-500"
              disabled={busy !== null}
              onClick={() => {
                if (window.confirm("Cancel this milestone?")) act("cancel", `/milestones/${id}/cancel`);
              }}
            >
              {m.paypal_authorization_id ? "Cancel and release the PayPal hold" : "Cancel milestone"}
            </Button>
          )}

          {/* ----------------------------------------------------- verification */}
          {verifying ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Live verification</CardTitle>
              </CardHeader>
              <CardContent>
                <VerificationTimeline milestoneId={m.id} criteria={m.criteria} onDone={load} />
              </CardContent>
            </Card>
          ) : report ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Verification report</CardTitle>
                <CardDescription>
                  Attempt {m.submissions.length} · {m.submissions.at(-1)?.live_url}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ReportView report={report} perspective={perspective} />
              </CardContent>
            </Card>
          ) : m.latest_run?.status === "error" ? (
            <Alert variant="destructive">
              <AlertDescription>Verification could not finish: {m.latest_run.error}</AlertDescription>
            </Alert>
          ) : null}
        </div>

        {/* ------------------------------------------------------------ sidebar */}
        <div className="space-y-6">
          <MoneyPanel m={m} perspective={perspective} />
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Acceptance criteria</CardTitle>
              <CardDescription>Agreed by both parties before funding</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {m.criteria.map((c) => (
                <div key={c.code} className="text-sm">
                  <p className="text-slate-800">{c.description}</p>
                  <p className="text-xs text-slate-500">
                    {c.code} · {CHECK_LABELS[c.check_type]}
                    {!c.is_objective && " · confirmed by the client"}
                  </p>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function SubmitForm({ busy, onSubmit }: { busy: string | null; onSubmit: (body: object) => void }) {
  const [liveUrl, setLiveUrl] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  return (
    <Card className="border-sky-200">
      <CardHeader>
        <CardTitle className="text-base">Deliver the work</CardTitle>
        <CardDescription>Submit the live site. Verification starts immediately and takes about a minute.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="live">Live site URL</Label>
          <Input id="live" placeholder="https://your-site.example" value={liveUrl} onChange={(e) => setLiveUrl(e.target.value)} />
          <div className="flex flex-wrap gap-2 pt-1">
            <span className="text-xs text-slate-500">Demo deliveries:</span>
            {DEMO_SITES.map((site) => (
              <button
                key={site.id}
                type="button"
                title={site.hint}
                className="rounded-full border px-2.5 py-0.5 text-xs text-slate-600 hover:bg-slate-100"
                onClick={() => setLiveUrl(demoSiteUrl(site.id))}
              >
                {site.label}
              </button>
            ))}
          </div>
        </div>
        <div className="space-y-2">
          <Label htmlFor="repo">GitHub repository (optional)</Label>
          <Input
            id="repo"
            placeholder="https://github.com/you/bakery-site"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
          />
        </div>
        <Button
          disabled={busy !== null || !liveUrl}
          onClick={() => onSubmit({ live_url: liveUrl.trim(), repo_url: repoUrl.trim() || null })}
        >
          {busy === "submit" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Submit for verification
        </Button>
      </CardContent>
    </Card>
  );
}

function Decisions({
  m,
  busy,
  act,
}: {
  m: MilestoneOut;
  busy: string | null;
  act: (label: string, path: string, json?: unknown) => void;
}) {
  const [reason, setReason] = useState("");
  const can = (action: string) => m.allowed_actions.includes(action);
  const base = `/milestones/${m.id}`;

  return (
    <Card className="border-emerald-200">
      <CardHeader>
        <CardTitle className="text-base">
          {m.state === "passed" && "The work passed. Release the payment?"}
          {m.state === "needs_review" && (can("review_pass") ? "Your review is needed" : "Waiting for the client's review")}
          {m.state === "failed" && "The freelancer can fix and resubmit"}
          {m.state === "releasing" && "Payment is being released"}
        </CardTitle>
        {m.state === "passed" && (
          <CardDescription>
            Approving captures the held PayPal payment and pays the freelancer. Also confirm the criteria marked
            &quot;confirmed by the client&quot;. If you don&apos;t respond, payment is released automatically after 48 hours.
          </CardDescription>
        )}
        {m.state === "needs_review" && can("review_pass") && (
          <CardDescription>
            The verifier wasn&apos;t confident or detected a manipulation attempt. Look at the evidence below and decide.
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-3">
          {can("approve_release") && (
            <Button disabled={busy !== null} onClick={() => act("approve", `${base}/approve-release`)}>
              {busy === "approve" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Approve and release payment
            </Button>
          )}
          {can("review_pass") && (
            <Button disabled={busy !== null} onClick={() => act("review", `${base}/review`, { decision: "pass" })}>
              Accept the work
            </Button>
          )}
          {can("review_fail") && (
            <Button variant="outline" disabled={busy !== null} onClick={() => act("review", `${base}/review`, { decision: "fail" })}>
              Send back for changes
            </Button>
          )}
          {can("retry_release") && (
            <Button disabled={busy !== null} onClick={() => act("retry", `${base}/retry-release`)}>
              Retry payout
            </Button>
          )}
        </div>
        {can("dispute") && (
          <div className="space-y-2 border-t pt-3">
            <Label htmlFor="reason">{m.state === "failed" ? "Disagree with the verdict?" : "Request changes instead"}</Label>
            <Textarea id="reason" placeholder="Explain what's wrong" value={reason} onChange={(e) => setReason(e.target.value)} />
            <Button
              variant="outline"
              size="sm"
              disabled={busy !== null || reason.trim().length < 5}
              onClick={() => act("dispute", `${base}/dispute`, { reason })}
            >
              Open a dispute
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
