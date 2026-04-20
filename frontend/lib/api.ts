// All requests go through Next.js /api/* rewrite → FastAPI on :9001

const BASE = "/api";

// ─── helpers ───────────────────────────────────────────────────────────────

function getToken(): string | null {
  if (typeof document === "undefined") return null;
  return (
    document.cookie
      .split("; ")
      .find((c) => c.startsWith("token="))
      ?.split("=")[1] ?? null
  );
}

function setToken(token: string): void {
  // httpOnly cannot be set from JS; we store in a regular cookie for simplicity.
  // For production, proxy through a Next.js API route that sets httpOnly.
  document.cookie = `token=${token}; path=/; max-age=${72 * 3600}; SameSite=Lax`;
}

export function clearToken(): void {
  document.cookie = "token=; path=/; max-age=0";
}

async function request<T>(
  path: string,
  init?: RequestInit & { auth?: boolean }
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string>),
  };

  if (init?.auth !== false) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}${path}`, { ...init, headers });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch (_) {}
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ─── types ─────────────────────────────────────────────────────────────────

export interface AuthResponse {
  user_id: string;
  token: string;
}

export interface Paper {
  paper_id: string;
  title: string;
  summary: string;
  categories: string;
  category_name?: string | null;
  submitted_at: string;
  score: number;
  link?: string;
}

export interface IndexedPaper {
  paper_id: string;
  title: string;
  summary: string | null;
  authors: string | null;
  categories: string | null;
  category_name: string | null;
  submitted_at: string | null;
  link: string | null;
  pdf_url: string | null;
}

export interface Thread {
  chat_id: string;
  title: string | null;
  focused_paper_ids?: string[];
  created_at: string;
  updated_at: string;
}

export interface ThreadScope {
  paper_ids: string[];
  papers: IndexedPaper[];
}

export interface Message {
  msg_id: number;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}

export interface Researcher {
  name: string;
}

// ─── auth ──────────────────────────────────────────────────────────────────

export async function register(
  email: string,
  password: string
): Promise<AuthResponse> {
  const data = await request<AuthResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
    auth: false,
  });
  setToken(data.token);
  return data;
}

export async function login(
  email: string,
  password: string
): Promise<AuthResponse> {
  const data = await request<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
    auth: false,
  });
  setToken(data.token);
  return data;
}

// ─── rank / discovery ──────────────────────────────────────────────────────

export async function rankPapers(params: {
  query?: string;
  category?: string[];
  window_days?: number;
  top_k?: number;
}): Promise<Paper[]> {
  const data = await request<{ results: Paper[] }>("/rank", {
    method: "POST",
    body: JSON.stringify(params),
  });
  return data.results;
}

export async function searchIndexedPapers(params: {
  q?: string;
  category?: string;
  limit?: number;
}): Promise<IndexedPaper[]> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.category) qs.set("category", params.category);
  if (params.limit) qs.set("limit", String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  const data = await request<{ results: IndexedPaper[] }>(`/papers${suffix}`);
  return data.results;
}

export async function indexArxivPaper(url: string): Promise<{ paper_id: string; status: string }> {
  return request<{ paper_id: string; status: string }>("/papers/index-arxiv", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

// ─── threads ───────────────────────────────────────────────────────────────

export async function listThreads(): Promise<Thread[]> {
  return request<Thread[]>("/threads");
}

export async function createThread(title?: string): Promise<Thread> {
  return request<Thread>("/threads", {
    method: "POST",
    body: JSON.stringify({ title: title ?? null, paper_id: null }),
  });
}

export async function createPaperThread(paperId: string, title?: string): Promise<Thread> {
  return request<Thread>("/threads", {
    method: "POST",
    body: JSON.stringify({ title: title ?? null, paper_id: paperId }),
  });
}

export async function deleteThread(chatId: string): Promise<void> {
  return request<void>(`/threads/${chatId}`, { method: "DELETE" });
}

export async function getThreadScope(chatId: string): Promise<ThreadScope> {
  return request<ThreadScope>(`/threads/${chatId}/scope`);
}

export async function updateThreadScope(chatId: string, paperIds: string[]): Promise<ThreadScope> {
  return request<ThreadScope>(`/threads/${chatId}/scope`, {
    method: "PUT",
    body: JSON.stringify({ paper_ids: paperIds }),
  });
}

// ─── messages ──────────────────────────────────────────────────────────────

export async function getMessages(chatId: string): Promise<Message[]> {
  return request<Message[]>(`/threads/${chatId}/messages`);
}

/**
 * Send a message and return an EventSource-like async generator of SSE events.
 * Each event is already parsed from JSON.
 */
export async function* streamMessage(
  chatId: string,
  content: string,
  idempotencyKey?: string,
  paperIds?: string[],
  thinkingMode: "fast" | "detailed" = "detailed",
  llmProvider?: "ollama" | "groq"
): AsyncGenerator<{ type: string; [k: string]: unknown }> {
  const token = getToken();
  const res = await fetch(`${BASE}/threads/${chatId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      content,
      idempotency_key: idempotencyKey ?? null,
      paper_ids: paperIds && paperIds.length ? paperIds : null,
      thinking_mode: thinkingMode,
      llm_provider: llmProvider ?? null,
    }),
  });

  if (!res.ok || !res.body) {
    throw new ApiError(res.status, "Stream failed");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    const lines = buf.split("\n");
    buf = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          yield JSON.parse(line.slice(6));
        } catch (_) {}
      }
    }
  }
}

// ─── researchers ───────────────────────────────────────────────────────────

export async function listResearchers(q?: string): Promise<Researcher[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return request<Researcher[]>(`/researchers${qs}`);
}
