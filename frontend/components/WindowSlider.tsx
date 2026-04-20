"use client";

interface WindowSliderProps {
  value: number;
  onChange: (days: number) => void;
}

const STEPS = [1, 3, 7, 14, 30];

export default function WindowSlider({ value, onChange }: WindowSliderProps) {
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="text-muted-foreground whitespace-nowrap">Last</span>
      <div className="flex gap-1">
        {STEPS.map((d) => (
          <button
            key={d}
            onClick={() => onChange(d)}
            className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
              value === d
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-foreground"
            }`}
          >
            {d === 1 ? "1d" : d < 30 ? `${d}d` : "1mo"}
          </button>
        ))}
      </div>
    </div>
  );
}
