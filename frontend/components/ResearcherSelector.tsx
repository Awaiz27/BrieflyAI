"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Search, User } from "lucide-react";
import { listResearchers, type Researcher } from "@/lib/api";

interface ResearcherSelectorProps {
  value: string;
  onChange: (name: string) => void;
}

export default function ResearcherSelector({ value, onChange }: ResearcherSelectorProps) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Researcher[]>([]);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    listResearchers(q || undefined)
      .then(setResults)
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, [open, q]);

  return (
    <div ref={ref} className="relative w-full">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm hover:border-primary/50 transition-colors"
      >
        <span className="flex items-center gap-2 truncate">
          <User className="h-4 w-4 shrink-0 text-muted-foreground" />
          {value || <span className="text-muted-foreground">Select a researcher…</span>}
        </span>
        <ChevronDown className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute top-full z-50 mt-1 w-full rounded-lg border border-border bg-card shadow-lg">
          {/* search */}
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
            <input
              autoFocus
              placeholder="Search by name…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>

          {/* list */}
          <ul className="max-h-48 overflow-y-auto py-1">
            {loading && (
              <li className="px-3 py-2 text-xs text-muted-foreground">Loading…</li>
            )}
            {!loading && results.length === 0 && (
              <li className="px-3 py-2 text-xs text-muted-foreground">No researchers found</li>
            )}
            {!loading &&
              results.map((r) => (
                <li key={r.name}>
                  <button
                    onClick={() => { onChange(r.name); setOpen(false); }}
                    className="w-full px-3 py-2 text-left text-sm hover:bg-accent transition-colors"
                  >
                    {r.name}
                  </button>
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  );
}
