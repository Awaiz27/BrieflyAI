"use client";

import { MessageSquare, Plus, Trash2 } from "lucide-react";
import type { Thread } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ThreadSidebarProps {
  threads: Thread[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
}

export default function ThreadSidebar({
  threads,
  activeId,
  onSelect,
  onCreate,
  onDelete,
}: ThreadSidebarProps) {
  return (
    <aside className="flex h-full flex-col border-r border-border bg-card">
      {/* header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <span className="text-sm font-semibold">Conversations</span>
        <Button size="icon" variant="ghost" onClick={onCreate} title="New chat">
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      {/* list */}
      <ul className="flex-1 overflow-y-auto py-1">
        {threads.length === 0 && (
          <li className="px-4 py-3 text-xs text-muted-foreground">
            No conversations yet. Click + to start.
          </li>
        )}
        {threads.map((t) => (
          <li key={t.chat_id}>
            <button
              onClick={() => onSelect(t.chat_id)}
              className={cn(
                "group flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm transition-colors",
                activeId === t.chat_id
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              )}
            >
              <MessageSquare className="h-4 w-4 shrink-0" />
              <span className="flex-1 truncate">
                {t.title ?? "Untitled"}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); onDelete(t.chat_id); }}
                className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-opacity"
                title="Delete"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
