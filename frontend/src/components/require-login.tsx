"use client";

import Link from "next/link";

import { useAuth } from "@/components/auth";
import { buttonVariants } from "@/components/ui/button";

export function RequireLogin({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  if (!ready) return <p className="text-slate-500">Loading...</p>;
  if (!user) {
    return (
      <div className="space-y-4 py-16 text-center">
        <p className="text-slate-600">Log in as the demo client or freelancer to continue.</p>
        <Link href="/" className={buttonVariants()}>
          Go to demo login
        </Link>
      </div>
    );
  }
  return <>{children}</>;
}
