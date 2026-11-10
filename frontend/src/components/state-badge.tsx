import { STATE_INFO, TONE_CLASSES } from "@/lib/format";
import type { MilestoneState } from "@/lib/types";
import { cn } from "@/lib/utils";

export function StateBadge({ state, className }: { state: MilestoneState; className?: string }) {
  const info = STATE_INFO[state];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium",
        TONE_CLASSES[info.tone],
        className,
      )}
    >
      {info.label}
    </span>
  );
}
