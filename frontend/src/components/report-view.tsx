"use client";

import { AlertTriangle, CheckCircle2, CircleHelp, ShieldAlert, UserCheck, Wrench, XCircle } from "lucide-react";

import { assetUrl } from "@/lib/api";
import { CHECK_LABELS } from "@/lib/format";
import type { CriterionVerdict, VerificationReport } from "@/lib/types";
import { cn } from "@/lib/utils";

const OVERALL = {
  passed: { title: "All automated checks passed", tone: "border-emerald-200 bg-emerald-50 text-emerald-900" },
  failed: { title: "Some criteria failed", tone: "border-rose-200 bg-rose-50 text-rose-900" },
  needs_review: { title: "A human needs to review this", tone: "border-amber-200 bg-amber-50 text-amber-900" },
};

function VerdictIcon({ verdict }: { verdict: CriterionVerdict["verdict"] }) {
  const cls = "mt-0.5 h-5 w-5 shrink-0";
  if (verdict === "pass") return <CheckCircle2 className={cn(cls, "text-emerald-600")} />;
  if (verdict === "fail") return <XCircle className={cn(cls, "text-rose-600")} />;
  if (verdict === "manual") return <UserCheck className={cn(cls, "text-slate-500")} />;
  return <CircleHelp className={cn(cls, "text-amber-600")} />;
}

export function ReportView({ report, perspective }: { report: VerificationReport; perspective: "client" | "freelancer" }) {
  const overall = OVERALL[report.overall];
  const failed = report.criteria.filter((c) => c.verdict === "fail");

  return (
    <div className="space-y-4">
      <div className={cn("rounded-lg border p-4", overall.tone)}>
        <p className="font-semibold">{overall.title}</p>
        <p className="text-sm opacity-80">
          {Object.entries(report.counts)
            .map(([verdict, n]) => `${n} ${verdict.replace("_", " ")}`)
            .join(" · ")}{" "}
          · verified in {(report.duration_ms / 1000).toFixed(1)}s
        </p>
      </div>

      {report.security_flags.length > 0 && (
        <div className="rounded-lg border border-rose-300 bg-rose-50 p-4 text-rose-900">
          <p className="flex items-center gap-2 font-semibold">
            <ShieldAlert className="h-5 w-5" />
            Manipulation attempt detected: automatic approval blocked
          </p>
          <p className="mb-2 text-sm">
            The submission contains instructions aimed at the AI verifier. They had no effect on the checks, but a
            human must review before any money moves.
          </p>
          <ul className="space-y-1 text-sm">
            {report.security_flags.slice(0, 5).map((flag, i) => (
              <li key={i} className="rounded bg-white/70 px-2 py-1 font-mono text-xs">
                [{flag.severity} · {flag.source.replace("_", " ")}] {flag.snippet}
              </li>
            ))}
          </ul>
        </div>
      )}

      {perspective === "freelancer" && failed.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <p className="mb-2 flex items-center gap-2 font-semibold text-amber-900">
            <Wrench className="h-5 w-5" /> What to fix before resubmitting
          </p>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-amber-900">
            {failed.map((c) => (
              <li key={c.criterion_id}>{c.fix_suggestion ?? c.check?.summary ?? c.description}</li>
            ))}
          </ol>
        </div>
      )}

      <div className="space-y-3">
        {report.criteria.map((c) => (
          <CriterionCard key={c.criterion_id} verdict={c} />
        ))}
      </div>
    </div>
  );
}

function CriterionCard({ verdict: c }: { verdict: CriterionVerdict }) {
  const measurements = Object.entries(c.check?.evidence.measurements ?? {}).filter(
    ([key, value]) => value !== null && value !== "" && !["new_text", "title"].includes(key),
  );
  return (
    <div className="rounded-lg border bg-white p-4">
      <div className="flex items-start gap-3">
        <VerdictIcon verdict={c.verdict} />
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-medium text-slate-900">
              <span className="mr-2 text-xs text-slate-400">{c.criterion_id}</span>
              {c.description}
            </p>
            <span className="text-xs text-slate-500">
              {CHECK_LABELS[c.check_type]}
              {c.verdict !== "manual" && ` · confidence ${(c.confidence * 100).toFixed(0)}% · by ${c.decided_by}`}
            </span>
          </div>
          {c.check && <p className="text-sm text-slate-700">{c.check.summary}</p>}
          <p className="text-sm text-slate-500">{c.reasoning}</p>
          {c.fix_suggestion && (
            <p className="flex gap-2 rounded bg-amber-50 p-2 text-sm text-amber-900">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {c.fix_suggestion}
            </p>
          )}
          {measurements.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {measurements.slice(0, 8).map(([key, value]) => (
                <span key={key} className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-600">
                  {key}={String(value)}
                </span>
              ))}
            </div>
          )}
          {c.check && c.check.evidence.screenshots.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {c.check.evidence.screenshots.slice(0, 4).map((src) => (
                <a key={src} href={assetUrl(src)} target="_blank" rel="noreferrer">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={assetUrl(src)}
                    alt={`Evidence for ${c.criterion_id}`}
                    className="h-24 w-40 rounded border object-cover object-top hover:ring-2 hover:ring-emerald-400"
                  />
                </a>
              ))}
            </div>
          )}
          {c.check && c.check.evidence.logs.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">Logs ({c.check.evidence.logs.length})</summary>
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2">
                {c.check.evidence.logs.join("\n")}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
