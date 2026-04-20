"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Bot, Check, Plus, X } from "lucide-react";
import {
  listThreads,
  createThread,
  deleteThread,
  getThreadScope,
  searchIndexedPapers,
  updateThreadScope,
  type IndexedPaper,
  type Thread,
} from "@/lib/api";
import Navbar from "@/components/Navbar";
import ThreadSidebar from "@/components/ThreadSidebar";
import ChatWindow from "@/components/ChatWindow";

export default function ChatPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center text-sm text-muted-foreground">Loading chat...</div>}>
      <ChatPageInner />
    </Suspense>
  );
}

function ChatPageInner() {
  const searchParams = useSearchParams();
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [paperSearch, setPaperSearch] = useState("");
  const [paperOptions, setPaperOptions] = useState<IndexedPaper[]>([]);
  const [selectedPaperIds, setSelectedPaperIds] = useState<string[]>([]);
  const [selectedPaperMap, setSelectedPaperMap] = useState<Record<string, string>>({});
  const scopeMutationSeq = useRef(0);

  // Load threads on mount
  useEffect(() => {
    const requestedChatId = searchParams.get("chat_id");
    listThreads()
      .then((ts) => {
        setThreads(ts);
        if (requestedChatId && ts.some((t) => t.chat_id === requestedChatId)) {
          setActiveChatId(requestedChatId);
        } else if (ts.length > 0) {
          setActiveChatId(ts[0].chat_id);
        }
      })
      .catch(console.error);
  }, [searchParams]);

  useEffect(() => {
    searchIndexedPapers({ q: paperSearch || undefined, limit: 20 })
      .then(setPaperOptions)
      .catch(console.error);
  }, [paperSearch]);

  useEffect(() => {
    if (!activeChatId) {
      setSelectedPaperIds([]);
      setSelectedPaperMap({});
      return;
    }

    getThreadScope(activeChatId)
      .then((scope) => {
        setSelectedPaperIds(scope.paper_ids ?? []);
        const map: Record<string, string> = {};
        for (const p of scope.papers ?? []) {
          map[p.paper_id] = p.title;
        }
        setSelectedPaperMap(map);
      })
      .catch((err) => {
        console.error(err);
        setSelectedPaperIds([]);
        setSelectedPaperMap({});
      });
  }, [activeChatId]);

  async function applyThreadScope(nextIds: string[], nextMap: Record<string, string>) {
    // Optimistically reflect UI state so message send always carries latest selection.
    setSelectedPaperIds(nextIds);
    setSelectedPaperMap(nextMap);

    if (!activeChatId) {
      return;
    }

    const seq = ++scopeMutationSeq.current;
    try {
      const scope = await updateThreadScope(activeChatId, nextIds);
      if (seq !== scopeMutationSeq.current) return;

      setSelectedPaperIds(scope.paper_ids ?? []);
      const map: Record<string, string> = {};
      for (const p of scope.papers ?? []) {
        map[p.paper_id] = p.title;
      }
      setSelectedPaperMap(map);
    } catch (err) {
      console.error(err);
      if (seq !== scopeMutationSeq.current) return;

      // Re-sync from server on failure to avoid drift.
      try {
        const scope = await getThreadScope(activeChatId);
        setSelectedPaperIds(scope.paper_ids ?? []);
        const map: Record<string, string> = {};
        for (const p of scope.papers ?? []) {
          map[p.paper_id] = p.title;
        }
        setSelectedPaperMap(map);
      } catch (syncErr) {
        console.error(syncErr);
      }
    }
  }

  function togglePaperSelection(paperId: string, paperTitle?: string) {
    const selected = selectedPaperIds.includes(paperId);
    const nextIds = selected
      ? selectedPaperIds.filter((id) => id !== paperId)
      : [...selectedPaperIds, paperId];

    const nextMap: Record<string, string> = { ...selectedPaperMap };
    if (selected) {
      delete nextMap[paperId];
    } else if (paperTitle) {
      nextMap[paperId] = paperTitle;
    }

    void applyThreadScope(nextIds, nextMap);
  }

  function removePaperSelection(paperId: string) {
    const nextIds = selectedPaperIds.filter((id) => id !== paperId);
    const nextMap: Record<string, string> = { ...selectedPaperMap };
    delete nextMap[paperId];
    void applyThreadScope(nextIds, nextMap);
  }

  const showOptions = paperSearch.trim().length > 0;

  async function handleCreate() {
    try {
      const thread = await createThread();
      setThreads((prev) => [thread, ...prev]);
      setActiveChatId(thread.chat_id);
    } catch (e) {
      console.error(e);
    }
  }

  async function handleDelete(chatId: string) {
    try {
      await deleteThread(chatId);
      setThreads((prev) => prev.filter((t) => t.chat_id !== chatId));
      if (activeChatId === chatId) {
        const remaining = threads.filter((t) => t.chat_id !== chatId);
        setActiveChatId(remaining[0]?.chat_id ?? null);
      }
    } catch (e) {
      console.error(e);
    }
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <Navbar />

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <div className="w-64 shrink-0 overflow-hidden">
          <ThreadSidebar
            threads={threads}
            activeId={activeChatId}
            onSelect={setActiveChatId}
            onCreate={handleCreate}
            onDelete={handleDelete}
          />
        </div>

        {/* Main area */}
        <div className="flex flex-1 flex-col overflow-hidden">
          <div className="border-b border-border bg-card px-4 py-3">
            <p className="mb-2 text-xs font-medium text-muted-foreground">
              Optional context boost: select one or more indexed papers for this query
            </p>
            <div className="relative">
              <input
                value={paperSearch}
                onChange={(e) => setPaperSearch(e.target.value)}
                placeholder="Type to search papers and click to add multiple"
                className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
              />

              {showOptions && (
                <div className="absolute z-20 mt-1 max-h-52 w-full overflow-y-auto rounded-md border border-border bg-background shadow-lg">
                  {paperOptions.length === 0 ? (
                    <p className="px-3 py-2 text-xs text-muted-foreground">No papers found.</p>
                  ) : (
                    paperOptions.map((p) => {
                      const selected = selectedPaperIds.includes(p.paper_id);
                      return (
                        <button
                          key={p.paper_id}
                          type="button"
                          onClick={() => togglePaperSelection(p.paper_id, p.title)}
                          className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs transition-colors ${
                            selected
                              ? "bg-primary/15 text-primary"
                              : "text-foreground hover:bg-card"
                          }`}
                        >
                          <span className="line-clamp-1">{p.title}</span>
                          <span className="shrink-0">
                            {selected ? <Check className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5 opacity-60" />}
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>
              )}
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              <span className="text-xs font-medium text-muted-foreground">
                Discussion scope ({selectedPaperIds.length})
              </span>
              {selectedPaperIds.length === 0 && (
                <span className="text-xs text-muted-foreground">No papers selected</span>
              )}
              {selectedPaperIds.map((pid) => (
                <span
                  key={pid}
                  className="inline-flex max-w-full items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-xs text-primary"
                >
                  <span className="max-w-[320px] truncate">
                    {selectedPaperMap[pid] ?? paperOptions.find((p) => p.paper_id === pid)?.title ?? pid}
                  </span>
                  <button
                    type="button"
                    onClick={() => removePaperSelection(pid)}
                    className="rounded-full p-0.5 hover:bg-primary/20"
                    aria-label="Remove selected paper"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
              {selectedPaperIds.length > 0 && (
                <button
                  type="button"
                  onClick={() => {
                    void applyThreadScope([], {});
                  }}
                  className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground hover:bg-card"
                >
                  Clear all
                </button>
              )}
            </div>
          </div>

          {/* Chat */}
          <div className="flex-1 overflow-hidden">
            {activeChatId ? (
              <ChatWindow chatId={activeChatId} selectedPaperIds={selectedPaperIds} />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
                <Bot className="h-12 w-12 opacity-30" />
                <p className="text-sm">Select a conversation or create a new one.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
