"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Sparkles } from "lucide-react";
import {
  createPaperThread,
  indexArxivPaper,
  searchIndexedPapers,
  type Paper,
} from "@/lib/api";
import Navbar from "@/components/Navbar";
import PaperCard from "@/components/PaperCard";
import CategoryTabs from "@/components/CategoryTabs";
import SearchBar from "@/components/SearchBar";
import WindowSlider from "@/components/WindowSlider";

export default function DiscoveryPage() {
  const router = useRouter();
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [indexing, setIndexing] = useState(false);
  const [arxivUrl, setArxivUrl] = useState("");

  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState(3);
  const [visibleCount, setVisibleCount] = useState(12);

  const fetch = useCallback(
    async (q: string, cat: string | null, days: number) => {
      setLoading(true);
      setError(null);
      try {
        const indexed = await searchIndexedPapers({
          q: q || undefined,
          category: cat || undefined,
          limit: 50,
        });
        const results: Paper[] = indexed.map((p) => ({
          paper_id: p.paper_id,
          title: p.title,
          summary: p.summary ?? "",
          categories: p.categories ?? "unknown",
          category_name: p.category_name,
          submitted_at: p.submitted_at ?? new Date().toISOString(),
          score: 1,
          link: p.link ?? undefined,
        }));
        setPapers(results);
        setVisibleCount(12);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to load papers");
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Initial load + re-fetch when filters change
  useEffect(() => {
    fetch(query, category, windowDays);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, windowDays]);

  function handleSearch() {
    fetch(query, category, windowDays);
  }

  function handleCategoryChange(cat: string | null) {
    setCategory(cat);
  }

  async function handleIndexArxiv() {
    if (!arxivUrl.trim()) return;
    setIndexing(true);
    setError(null);
    try {
      await indexArxivPaper(arxivUrl.trim());
      setArxivUrl("");
      await fetch(query, category, windowDays);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to index arXiv paper");
    } finally {
      setIndexing(false);
    }
  }

  async function handleDiscussPaper(p: Paper) {
    try {
      const thread = await createPaperThread(p.paper_id);
      router.push(`/chat?chat_id=${encodeURIComponent(thread.chat_id)}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to open paper chat");
    }
  }

  return (
    <div className="min-h-screen flex flex-col">
      <Navbar />

      <main className="flex-1 mx-auto w-full max-w-7xl px-4 py-8">
        {/* Hero */}
        <div className="mb-8">
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <Sparkles className="h-6 w-6 text-primary" />
            Indexed Paper Discovery
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Search papers already indexed, or add a new arXiv paper by link.
          </p>
        </div>

        <div className="mb-6 rounded-xl border border-border bg-card p-4">
          <p className="mb-2 text-sm font-medium">Index a specific arXiv paper</p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              value={arxivUrl}
              onChange={(e) => setArxivUrl(e.target.value)}
              placeholder="https://arxiv.org/abs/2401.12345"
              className="h-10 flex-1 rounded-md border border-border bg-background px-3 text-sm"
            />
            <button
              onClick={handleIndexArxiv}
              disabled={indexing}
              className="h-10 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-60"
            >
              {indexing ? "Indexing..." : "Index Paper"}
            </button>
          </div>
        </div>

        {/* Controls */}
        <div className="mb-6 space-y-3">
          <SearchBar value={query} onChange={setQuery} onSearch={handleSearch} />

          <div className="flex flex-wrap items-center justify-between gap-3">
            <CategoryTabs selected={category} onChange={handleCategoryChange} />
            <WindowSlider value={windowDays} onChange={setWindowDays} />
          </div>
        </div>

        {/* Results */}
        {loading && (
          <div className="flex items-center justify-center py-24">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
          </div>
        )}

        {!loading && error && (
          <div className="rounded-lg border border-red-900/50 bg-red-900/10 px-4 py-3 text-sm text-red-400">
            {error}
          </div>
        )}

        {!loading && !error && papers.length === 0 && (
          <div className="flex flex-col items-center justify-center py-24 text-muted-foreground">
            <p className="text-base">No papers found for this filter.</p>
            <p className="mt-1 text-sm">Try widening the time window or removing the category filter.</p>
          </div>
        )}

        {!loading && papers.length > 0 && (
          <>
            <p className="mb-4 text-xs text-muted-foreground">
              {papers.length} paper{papers.length !== 1 ? "s" : ""} found
            </p>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {papers.slice(0, visibleCount).map((p) => (
                <PaperCard key={p.paper_id} paper={p} onDiscuss={handleDiscussPaper} />
              ))}
            </div>
            {visibleCount < papers.length && (
              <div className="mt-5 flex justify-center">
                <button
                  className="rounded-md border border-border px-4 py-2 text-sm hover:bg-card"
                  onClick={() => setVisibleCount((v) => v + 12)}
                >
                  Show More
                </button>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
