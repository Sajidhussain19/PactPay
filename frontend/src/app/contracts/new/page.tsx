"use client";

import { Loader2, Sparkles, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { RequireLogin } from "@/components/require-login";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { CHECK_LABELS, money } from "@/lib/format";
import type { ContractOut, DraftContract } from "@/lib/types";

const EXAMPLE =
  "Landing page for my bakery: menu with prices, contact form that emails me, works on mobile, loads fast, matches this mockup.";

export default function NewContractPage() {
  return (
    <RequireLogin>
      <NewContract />
    </RequireLogin>
  );
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function NewContract() {
  const router = useRouter();
  const [job, setJob] = useState(EXAMPLE);
  const [budget, setBudget] = useState("150");
  const [mockup, setMockup] = useState<string | null>(null);
  const [freelancerEmail, setFreelancerEmail] = useState("freelancer@pactpay.demo");
  const [draft, setDraft] = useState<DraftContract | null>(null);
  const [busy, setBusy] = useState<"draft" | "create" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function generate() {
    setBusy("draft");
    setError(null);
    try {
      setDraft(
        await api<DraftContract>("/contracts/draft", {
          method: "POST",
          json: { job_description: job, budget, currency: "USD", mockup_base64: mockup },
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  function update(mutate: (d: DraftContract) => void) {
    if (!draft) return;
    const copy: DraftContract = structuredClone(draft);
    mutate(copy);
    copy.total = copy.milestones.reduce((sum, m) => sum + Number(m.amount || 0), 0).toFixed(2);
    setDraft(copy);
  }

  async function create() {
    if (!draft) return;
    setBusy("create");
    setError(null);
    try {
      const contract = await api<ContractOut>("/contracts", {
        method: "POST",
        json: { contract: draft, freelancer_email: freelancerEmail || null, mockup_base64: mockup },
      });
      await api(`/contracts/${contract.id}/approve`, { method: "POST" });
      router.push(`/contracts/${contract.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">New contract</h1>
        <p className="text-slate-500">
          Describe the job in plain language. PactPay drafts milestones and acceptance criteria a program can check.
        </p>
      </div>

      <Card>
        <CardContent className="grid gap-4 pt-6 md:grid-cols-[1fr_220px]">
          <div className="space-y-2">
            <Label htmlFor="job">What do you need?</Label>
            <Textarea id="job" rows={4} value={job} onChange={(e) => setJob(e.target.value)} />
          </div>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="budget">Budget (USD)</Label>
              <Input id="budget" type="number" min="1" value={budget} onChange={(e) => setBudget(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="mockup">Mockup image (optional)</Label>
              <Input
                id="mockup"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  setMockup(file ? await readFileAsBase64(file) : null);
                }}
              />
            </div>
          </div>
          <div className="md:col-span-2">
            <Button disabled={busy !== null || job.length < 20 || !budget} onClick={generate}>
              {busy === "draft" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
              {busy === "draft" ? "Drafting with AI..." : "Draft the contract"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {busy === "draft" && (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}

      {draft && busy !== "draft" && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>{draft.title}</CardTitle>
              <CardDescription>
                {draft.summary} · total {money(draft.total)}
              </CardDescription>
            </CardHeader>
            {draft.warnings.length > 0 && (
              <CardContent>
                <Alert>
                  <AlertDescription>
                    <ul className="list-disc pl-5 text-sm">
                      {draft.warnings.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              </CardContent>
            )}
          </Card>

          {draft.milestones.map((m, mi) => (
            <Card key={m.id}>
              <CardHeader className="grid gap-3 md:grid-cols-[1fr_140px]">
                <Input
                  value={m.title}
                  onChange={(e) => update((d) => void (d.milestones[mi].title = e.target.value))}
                  className="font-medium"
                />
                <Input
                  type="number"
                  value={m.amount}
                  onChange={(e) => update((d) => void (d.milestones[mi].amount = e.target.value))}
                />
              </CardHeader>
              <CardContent className="space-y-2">
                {m.criteria.map((c, ci) => (
                  <div key={c.id} className="flex items-start gap-3 rounded-md border p-3">
                    <div className="flex-1 space-y-1">
                      <Input
                        value={c.description}
                        onChange={(e) => update((d) => void (d.milestones[mi].criteria[ci].description = e.target.value))}
                      />
                      <p className="text-xs text-slate-500">
                        {c.id} · {CHECK_LABELS[c.check_type]}
                        {c.is_objective ? " · checked automatically" : " · you confirm this yourself"} · {c.rationale}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Remove criterion"
                      onClick={() => update((d) => void d.milestones[mi].criteria.splice(ci, 1))}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </CardContent>
            </Card>
          ))}

          <Card>
            <CardContent className="flex flex-wrap items-end gap-4 pt-6">
              <div className="space-y-2">
                <Label htmlFor="freelancer">Freelancer&apos;s PactPay email</Label>
                <Input
                  id="freelancer"
                  className="w-72"
                  value={freelancerEmail}
                  onChange={(e) => setFreelancerEmail(e.target.value)}
                />
              </div>
              <Button disabled={busy !== null} onClick={create}>
                {busy === "create" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Create and approve ({money(draft.total)})
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
