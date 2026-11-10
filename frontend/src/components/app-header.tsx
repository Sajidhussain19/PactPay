"use client";

import { ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { useAuth } from "@/components/auth";
import { Button } from "@/components/ui/button";
import type { Role } from "@/lib/types";

const ROLE_LABEL: Record<Role, string> = { client: "Client", freelancer: "Freelancer", admin: "Reviewer" };

export function AppHeader() {
  const { user, login, logout } = useAuth();
  const router = useRouter();

  async function switchTo(role: Role) {
    await login(role);
    router.refresh();
  }

  return (
    <header className="border-b bg-white/80 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold text-slate-900">
          <ShieldCheck className="h-6 w-6 text-emerald-600" />
          PactPay
        </Link>
        {user ? (
          <nav className="flex items-center gap-2 text-sm">
            <Link href="/dashboard" className="rounded-md px-3 py-2 text-slate-600 hover:bg-slate-100">
              Dashboard
            </Link>
            {user.role === "client" && (
              <Link href="/contracts/new" className="rounded-md px-3 py-2 text-slate-600 hover:bg-slate-100">
                New contract
              </Link>
            )}
            <span className="ml-2 hidden rounded-full bg-slate-100 px-3 py-1 text-slate-700 sm:inline">
              {user.name}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => switchTo(user.role === "client" ? "freelancer" : "client")}
            >
              Switch to {user.role === "client" ? ROLE_LABEL.freelancer : ROLE_LABEL.client}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                logout();
                router.push("/");
              }}
            >
              Log out
            </Button>
          </nav>
        ) : (
          <span className="text-sm text-slate-500">AI-verified escrow, powered by PayPal</span>
        )}
      </div>
    </header>
  );
}
