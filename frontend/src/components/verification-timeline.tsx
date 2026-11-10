"use client";

import { CheckCircle2, Loader2, ShieldAlert, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { assetUrl, streamUrl } from "@/lib/api";
import { CHECK_LABELS } from "@/lib/format";
import type { CriterionOut, VerificationEvent } from "@/lib/types";

interface Row {
  code: string;
  label: string;
  description: string;
  status: "running" | "passed" | "failed" | "unknown";
  summary?: string;
  verdict?: string;
  screenshot?: string;
}

/** Live view of a verification run, streamed over Server-Sent Events. */
export function VerificationTimeline({
  milestoneId,
  criteria,
  onDone,
}: {
  milestoneId: string;
  criteria: CriterionOut[];
  onDone: () => void;
}) {
  const [rows, setRows] = useState<Record<string, Row>>({});
  const [flags, setFlags] = useState<string[]>([]);
  const [started, setStarted] = useState(false);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    const byCode = Object.fromEntries(criteria.map((c) => [c.code, c]));
    const source = new EventSource(streamUrl(milestoneId));
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as VerificationEvent;
      if (event.type === "started") setStarted(true);
      if (event.type === "security_flag" && event.summary) setFlags((f) => [...f, event.summary!]);
      if (event.type === "report_ready") {
        source.close();
        doneRef.current();
        return;
      }
      const code = event.criterion_id;
      if (!code) return;
      setRows((current) => {
        const previous = current[code] ?? {
          code,
          label: CHECK_LABELS[event.check_type ?? ""] ?? event.check_type ?? "",
          description: byCode[code]?.description ?? "",
          status: "running" as const,
        };
        const next = { ...previous };
        if (event.type === "check_finished") {
          next.status = (event.status as Row["status"]) ?? "unknown";
          next.summary = event.summary;
          next.screenshot = event.screenshot;
        }
        if (event.type === "judged") next.verdict = event.status;
        return { ...current, [code]: next };
      });
    };
    source.onerror = () => source.close();
    return () => source.close();
  }, [milestoneId, criteria]);

  const ordered = criteria.map((c) => rows[c.code]).filter(Boolean) as Row[];

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm text-slate-600">
        <Loader2 className="h-4 w-4 animate-spin text-sky-600" />
        {started ? "Checking the live site in a real browser..." : "Starting verification..."}
      </div>
      <ol className="space-y-2">
        {ordered.map((row) => (
          <li key={row.code} className="flex items-start gap-3 rounded-lg border bg-white p-3">
            {row.status === "running" && <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-sky-600" />}
            {row.status === "passed" && <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />}
            {row.status === "failed" && <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" />}
            {row.status === "unknown" && <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />}
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-slate-900">
                {row.label} <span className="font-normal text-slate-500">· {row.description}</span>
              </p>
              {row.summary && <p className="text-sm text-slate-600">{row.summary}</p>}
              {row.verdict && <p className="text-xs text-slate-500">AI judge: {row.verdict.replace("_", " ")}</p>}
            </div>
            {row.screenshot && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={assetUrl(row.screenshot)} alt="" className="h-14 w-24 shrink-0 rounded border object-cover object-top" />
            )}
          </li>
        ))}
      </ol>
      {flags.length > 0 && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
          <p className="font-medium">Manipulation attempt detected</p>
          {flags.slice(0, 3).map((flag) => (
            <p key={flag} className="truncate">{flag}</p>
          ))}
        </div>
      )}
    </div>
  );
}
