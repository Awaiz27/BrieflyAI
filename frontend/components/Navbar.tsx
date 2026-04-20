"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { BookOpen, Bot, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { clearToken } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function Navbar() {
  const pathname = usePathname();
  const router = useRouter();

  function handleLogout() {
    clearToken();
    router.push("/login");
  }

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur-sm">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2 font-bold text-lg tracking-tight">
          <BookOpen className="h-5 w-5 text-primary" />
          BrieflyAI
        </Link>

        {/* Nav links */}
        <nav className="flex items-center gap-1">
          <Link href="/">
            <Button
              variant="ghost"
              size="sm"
              className={cn(pathname === "/" && "bg-accent")}
            >
              Discover
            </Button>
          </Link>
          <Link href="/chat">
            <Button
              variant="ghost"
              size="sm"
              className={cn(pathname.startsWith("/chat") && "bg-accent")}
            >
              <Bot className="h-4 w-4" />
              AI Researcher
            </Button>
          </Link>
        </nav>

        {/* Logout */}
        <Button variant="ghost" size="icon" onClick={handleLogout} title="Log out">
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
