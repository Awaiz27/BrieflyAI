"use client";

import { cn } from "@/lib/utils";

const CATEGORIES = [
  { label: "All", value: null },
  { label: "Artificial Intelligence", value: "cs.AI" },
  { label: "Machine Learning", value: "cs.LG" },
  { label: "Computation and Language", value: "cs.CL" },
  { label: "Computer Vision", value: "cs.CV" },
  { label: "Neural and Evolutionary Computing", value: "cs.NE" },
  { label: "Robotics", value: "cs.RO" },
  { label: "Machine Learning (Statistics)", value: "stat.ML" },
];

interface CategoryTabsProps {
  selected: string | null;
  onChange: (cat: string | null) => void;
}

export default function CategoryTabs({ selected, onChange }: CategoryTabsProps) {
  return (
    <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-none">
      {CATEGORIES.map(({ label, value }) => (
        <button
          key={label}
          onClick={() => onChange(value)}
          className={cn(
            "shrink-0 rounded-full border px-3.5 py-1 text-sm font-medium transition-colors",
            selected === value
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground"
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
