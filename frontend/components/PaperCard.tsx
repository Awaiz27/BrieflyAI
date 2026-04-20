import type { Paper } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { ExternalLink } from "lucide-react";

interface PaperCardProps {
  paper: Paper;
  onDiscuss?: (paper: Paper) => void;
}

export default function PaperCard({ paper, onDiscuss }: PaperCardProps) {
  const date = new Date(paper.submitted_at).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  const scorePercent = Math.round(Math.min(paper.score, 1) * 100);
  const showScore =
    process.env.NEXT_PUBLIC_SHOW_DEBUG_SCORE === "true" ||
    process.env.NODE_ENV !== "production";

  return (
    <article
      className="group cursor-pointer rounded-xl border border-border bg-card p-5 transition-all hover:border-primary/40 hover:shadow-md hover:shadow-primary/5"
      onClick={() => onDiscuss?.(paper)}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <Badge variant="default" className="shrink-0 text-xs">
          {paper.category_name || paper.categories || "Uncategorized"}
        </Badge>
        <span className="text-xs text-muted-foreground whitespace-nowrap">{date}</span>
      </div>

      {/* Title */}
      <h2 className="mt-3 text-base font-semibold leading-snug text-foreground group-hover:text-primary transition-colors line-clamp-2">
        {paper.title}
      </h2>

      {/* Summary */}
      <p className="mt-2 text-sm text-muted-foreground leading-relaxed line-clamp-3">
        {paper.summary}
      </p>

      {/* Footer */}
      <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground">
        {showScore ? (
          <span className="font-medium">
            Score: <span className="text-primary">{scorePercent}%</span>
          </span>
        ) : (
          <span />
        )}
        <a
          href={paper.link ?? `https://arxiv.org/abs/${paper.paper_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1 hover:text-primary transition-colors"
          onClick={(e) => e.stopPropagation()}
        >
          arXiv <ExternalLink className="h-3 w-3" />
        </a>
      </div>

      <button
        className="mt-3 w-full rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/20"
        onClick={(e) => {
          e.stopPropagation();
          onDiscuss?.(paper);
        }}
      >
        Discuss This Paper
      </button>
    </article>
  );
}
