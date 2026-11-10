// Mirrors the backend response models (backend/app/api/schemas.py, verification/report.py).

export type Role = "client" | "freelancer" | "admin";

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
}

export type MilestoneState =
  | "draft"
  | "agreed"
  | "funded"
  | "submitted"
  | "verifying"
  | "passed"
  | "failed"
  | "needs_review"
  | "disputed"
  | "releasing"
  | "released"
  | "refunded"
  | "cancelled";

export type CheckType =
  | "page_loads"
  | "element_present"
  | "form_submits"
  | "responsive_layout"
  | "performance"
  | "visual_match"
  | "repo_check"
  | "tests_pass"
  | "manual";

export interface CriterionOut {
  code: string;
  description: string;
  check_type: CheckType;
  parameters: Record<string, unknown>;
  is_objective: boolean;
  rationale: string;
}

export interface SubmissionOut {
  attempt: number;
  live_url: string;
  repo_url: string | null;
  notes: string | null;
  submitted_at: string;
}

export interface Evidence {
  screenshots: string[];
  logs: string[];
  measurements: Record<string, string | number | boolean | null>;
}

export interface CheckResult {
  criterion_id: string;
  check_type: CheckType;
  passed: boolean | null;
  summary: string;
  evidence: Evidence;
  error: string | null;
  duration_ms: number;
}

export type Verdict = "pass" | "fail" | "needs_review" | "manual";

export interface CriterionVerdict {
  criterion_id: string;
  description: string;
  check_type: CheckType;
  verdict: Verdict;
  confidence: number;
  reasoning: string;
  decided_by: "judge" | "rules" | "human";
  fix_suggestion: string | null;
  check: CheckResult | null;
}

export interface SecurityFlag {
  kind: string;
  severity: "medium" | "high";
  source: string;
  location: string;
  pattern: string;
  snippet: string;
}

export interface VerificationReport {
  milestone_id: string;
  overall: "passed" | "failed" | "needs_review";
  criteria: CriterionVerdict[];
  counts: Record<string, number>;
  duration_ms: number;
  security_flags: SecurityFlag[];
}

export interface RunOut {
  id: string;
  status: "running" | "completed" | "error";
  overall: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  error: string | null;
  report: VerificationReport | null;
}

export interface MilestoneOut {
  id: string;
  contract_id: string;
  code: string;
  title: string;
  amount: string;
  currency: string;
  platform_fee: string;
  freelancer_receives: string;
  due_date: string;
  state: MilestoneState;
  paypal_order_id: string | null;
  paypal_authorization_id: string | null;
  authorization_expires_at: string | null;
  paypal_capture_id: string | null;
  payout_batch_id: string | null;
  payout_status: string | null;
  passed_at: string | null;
  released_at: string | null;
  allowed_actions: string[];
  criteria: CriterionOut[];
  submissions: SubmissionOut[];
  latest_run: RunOut | null;
}

export interface ContractOut {
  id: string;
  title: string;
  summary: string;
  currency: string;
  total: string;
  status: "draft" | "agreed" | "cancelled" | "completed";
  client: User;
  freelancer: User | null;
  client_approved_at: string | null;
  freelancer_approved_at: string | null;
  warnings: string[];
  created_at: string;
  milestones: MilestoneOut[];
}

// Contract draft produced by the AI contract agent (POST /contracts/draft)
export interface DraftCriterion {
  id: string;
  description: string;
  check_type: CheckType;
  parameters: Record<string, unknown>;
  is_objective: boolean;
  rationale: string;
}

export interface DraftMilestone {
  id: string;
  title: string;
  amount: string;
  due_date: string;
  criteria: DraftCriterion[];
}

export interface DraftContract {
  title: string;
  summary: string;
  currency: string;
  total: string;
  milestones: DraftMilestone[];
  warnings: string[];
}

export interface VerificationEvent {
  type: "started" | "check_started" | "check_finished" | "judged" | "security_flag" | "report_ready";
  criterion_id?: string;
  check_type?: CheckType;
  status?: string;
  summary?: string;
  screenshot?: string;
}
