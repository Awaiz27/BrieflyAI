"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getMessages, streamMessage, type Message } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

interface ChatWindowProps {
  chatId: string;
  selectedPaperIds?: string[];
}

export default function ChatWindow({ chatId, selectedPaperIds = [] }: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [thinkingMode, setThinkingMode] = useState<"fast" | "detailed">("detailed");
  const [llmProvider, setLlmProvider] = useState<"ollama" | "groq">("ollama");
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  // Load existing messages when thread changes
  useEffect(() => {
    setMessages([]);
    setStreamingText("");
    getMessages(chatId).then(setMessages).catch(console.error);
  }, [chatId]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingText]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || streaming) return;

    // Optimistically add user message
    const optimistic: Message = {
      msg_id: Date.now(),
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);
    setInput("");
    setStreaming(true);
    setStreamingText("");

    let answer = "";
    let latestDraft = "";
    try {
      for await (const event of streamMessage(chatId, text, undefined, selectedPaperIds, thinkingMode, llmProvider)) {
        if (event.type === "error") {
          const detail = typeof event.detail === "string" ? event.detail : "Something went wrong. Please try again.";
          answer = `⚠️ ${detail}`;
          break;
        }

        if (event.type === "delta") {
          const draft = (event.draft as string | null | undefined) ?? "";
          if (draft) {
            latestDraft = draft;
            setStreamingText(draft);
          }
          continue;
        }

        if (event.type === "done") {
          answer = (event.answer as string) ?? "";
          break;
        }
      }
    } catch (err) {
      answer = err instanceof Error ? `⚠️ ${err.message}` : "⚠️ Something went wrong. Please try again.";
    } finally {
      if (!answer && latestDraft) {
        answer = latestDraft;
      }
      setStreaming(false);
      setStreamingText("");
      if (answer) {
        setMessages((prev) => [
          ...prev,
          {
            msg_id: Date.now() + 1,
            role: "assistant",
            content: answer,
            created_at: new Date().toISOString(),
          },
        ]);
      }
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as unknown as FormEvent);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 && !streaming && (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Ask the AI researcher anything about recent papers…
          </div>
        )}

        {messages.map((m) => (
          <ChatBubble key={m.msg_id} message={m} />
        ))}

        {streaming && (
          <div className="flex justify-start">
            <div className="max-w-[80%] rounded-2xl rounded-tl-sm bg-card border border-border px-4 py-3 text-sm">
              {streamingText ? (
                <p className="streaming-cursor">{streamingText}</p>
              ) : (
                <span className="flex gap-1 text-muted-foreground">
                  <span className="animate-bounce">●</span>
                  <span className="animate-bounce [animation-delay:0.15s]">●</span>
                  <span className="animate-bounce [animation-delay:0.3s]">●</span>
                </span>
              )}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-border bg-background px-4 py-3 flex gap-2 items-end"
      >
        <div className="flex flex-col gap-2">
          <div className="flex gap-2">
            {/* Thinking mode */}
            <div className="flex gap-1 rounded-md border border-border bg-card p-1">
              <Button
                type="button"
                size="sm"
                variant={thinkingMode === "fast" ? "default" : "ghost"}
                disabled={streaming}
                onClick={() => setThinkingMode("fast")}
              >
                Fast Answer
              </Button>
              <Button
                type="button"
                size="sm"
                variant={thinkingMode === "detailed" ? "default" : "ghost"}
                disabled={streaming}
                onClick={() => setThinkingMode("detailed")}
              >
                Detailed Thinking
              </Button>
            </div>
            {/* Model selector */}
            <div className="flex items-center gap-2 rounded-md border border-border bg-card px-2 py-1">
              <span className="text-xs text-muted-foreground">Model</span>
              <select
                value={llmProvider}
                onChange={(e) => setLlmProvider(e.target.value as "ollama" | "groq")}
                disabled={streaming}
                className="h-8 rounded-md border border-border bg-background px-2 text-sm text-foreground focus:outline-none"
                aria-label="Select model provider"
              >
                <option value="ollama">Ollama (qwen3:0.6b-q4_K_M)</option>
                <option value="groq">Groq (openai/gpt-oss-20b)</option>
              </select>
            </div>
          </div>
          <span className="px-1 text-[11px] text-muted-foreground">
            {thinkingMode === "fast"
              ? "Reviewer off for lower latency"
              : "Reviewer on for higher quality"}
            {" · "}
            {llmProvider === "groq" ? "Groq cloud (fast)" : "Ollama local"}
          </span>
        </div>
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a question… (Shift+Enter for newline)"
          rows={2}
          className="flex-1 resize-none"
          disabled={streaming}
        />
        <Button type="submit" size="icon" disabled={!input.trim() || streaming}>
          <Send className="h-4 w-4" />
        </Button>
      </form>
    </div>
  );
}

function ChatBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed",
          isUser
            ? "rounded-tr-sm bg-primary text-primary-foreground"
            : "rounded-tl-sm bg-card border border-border text-foreground"
        )}
      >
        {isUser ? (
          <div className="whitespace-pre-wrap">{message.content}</div>
        ) : (
          <div className="markdown-body">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ ...props }) => (
                  <a {...props} target="_blank" rel="noreferrer noopener" />
                ),
                code: ({ className, children, ...props }) => {
                  const isInline = !(className || "").includes("language-");
                  if (isInline) {
                    return (
                      <code className="rounded bg-muted px-1 py-0.5 text-[0.92em]" {...props}>
                        {children}
                      </code>
                    );
                  }
                  return (
                    <code className={className} {...props}>
                      {children}
                    </code>
                  );
                },
              }}
            >
              {message.content}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
