"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useEffect, useRef, useState } from "react";

import SellerTable from "@/components/SellerTable";
import {
  cancelStream,
  clearToken,
  connectStream,
  createChatSession,
  deleteSession,
  fetchMe,
  getToken,
  getSessionMessages,
  listSessions,
  renameSession,
  streamChat,
  type AuthMe,
  type ChatSession,
  type ProgressStep,
  type QuotaMeta,
  type Seller,
} from "@/lib/api";

type Msg = {
  role: "user" | "assistant";
  text: string;
  sellers?: Seller[];
  progress?: ProgressStep[];
  quota?: QuotaMeta;
};

// 进入对话页 / 新建会话时 agent 主动给出的能力引导（静态欢迎语，用户输入前先看到）
const WELCOME: Msg = {
  role: "assistant",
  text:
    "你好！我是**亚马逊卖家获客助手**，按「**目标市场 + 品类**」帮你找潜在卖家（含联系方式、评分、国籍）。\n\n" +
    "**示例输入**\n" +
    "- 美国站卖杯子的2个中国卖家\n\n" +
    "**额度**：免费用户每月 10 个，初级 200 个；额度用完可联系管理员升级。\n\n" +
    "请输入目标市场 + 品类开始查询。",
};

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([WELCOME]);
  const [input, setInput] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [user, setUser] = useState<AuthMe | null>(null);
  const [pendingInterrupt, setPendingInterrupt] = useState<{
    question: string;
    current: { marketplace?: string; category?: string };
  } | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const streamControllerRef = useRef<AbortController | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    (async () => {
      const u = await fetchMe();
      if (!u) {
        window.location.href = "/login";
        return;
      }
      setUser(u);
      try {
        const ss = await listSessions();
        setSessions(ss);
        if (ss.length > 0) await loadSession(ss[0].session_id);
      } catch {
        /* 首次无会话 */
      }
    })();
  }, []);

  async function loadSession(sid: string) {
    setThreadId(sid);
    setPendingInterrupt(null);
    try {
      const msgs = await getSessionMessages(sid);
      if (msgs.length === 0) {
        setMessages([WELCOME]); // 空会话：显示欢迎语引导
        return;
      }
      setMessages(
        msgs.map((m) => ({
          role: m.role as "user" | "assistant",
          text: m.content,
          sellers: m.meta?.sellers,
          progress: m.meta?.progress,
        })),
      );
      // 断点续传：最后一条 assistant 消息没有 done 标记，重连 SSE 获取快照
      const lastMsg = msgs[msgs.length - 1];
      if (lastMsg && lastMsg.role === "assistant" && lastMsg.meta && !lastMsg.meta.done) {
        setLoading(true);
        // 先把已有内容清空，SSE snapshot 会重新填充（避免重复）
        setMessages((prev) => {
          const ms = [...prev];
          const i = ms.length - 1;
          if (i >= 0 && ms[i].role === "assistant") {
            ms[i] = { ...ms[i], text: "" };
          }
          return ms;
        });
        attachStream(sid);
      }
    } catch {
      setMessages([]);
    }
  }

  // SSE done 带 need_input：信息不全，进入补充 UI（pendingInterrupt 驱动输入框）
  function handleNeedInput(p: { missing?: string[]; marketplace?: string; category?: string }) {
    const missing = p.missing && p.missing.length > 0 ? p.missing : ["目标市场", "产品品类"];
    setPendingInterrupt({
      question: `请补充：${missing.join("、")}`,
      current: { marketplace: p.marketplace, category: p.category },
    });
    setLoading(false);
    streamControllerRef.current = null;
  }

  // 统一接 SSE（send / resume / 断点续传共用）：delta/sellers/progress 流式渲染，done 收尾
  function attachStream(tid: string) {
    const ctrl = connectStream(
      tid,
      (delta) => {
        setMessages((m) => {
          const last = m[m.length - 1];
          if (last && last.role === "assistant") {
            return [...m.slice(0, -1), { ...last, text: last.text + delta }];
          }
          return [...m, { role: "assistant", text: delta }];
        });
      },
      (sellers) => {
        setMessages((m) => {
          const last = m[m.length - 1];
          if (last && last.role === "assistant") {
            return [...m.slice(0, -1), { ...last, sellers }];
          }
          return [...m, { role: "assistant", text: "", sellers }];
        });
      },
      (progress) => {
        setMessages((m) => {
          const last = m[m.length - 1];
          if (last && last.role === "assistant") {
            return [...m.slice(0, -1), { ...last, progress }];
          }
          return m;
        });
      },
      (_sellers, _cancelled, quota) => {
        setLoading(false);
        streamControllerRef.current = null;
        setMessages((m) => {
          const last = m[m.length - 1];
          if (last && last.role === "assistant") {
            return [...m.slice(0, -1), { ...last, quota }];
          }
          return m;
        });
        listSessions().then(setSessions).catch(() => {});
      },
      handleNeedInput,
      (err) => {
        setMessages((m) => [...m.slice(0, -1), { role: "assistant", text: `错误：${err}` }]);
        setLoading(false);
        streamControllerRef.current = null;
      },
    );
    streamControllerRef.current = ctrl;
    return ctrl;
  }

  async function pause() {
    if (!threadId) return;
    try {
      await cancelStream(threadId);
    } catch {
      // ignore
    }
    if (streamControllerRef.current) {
      streamControllerRef.current.abort();
      streamControllerRef.current = null;
    }
    setLoading(false);
  }

  async function send() {
    const q = input.trim();
    if (!q || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: q }]);
    setLoading(true);
    try {
      const res = await streamChat(q, threadId ?? undefined);
      if (res.status === "streaming" && res.thread_id) {
        setThreadId(res.thread_id);
        setMessages((m) => [...m, { role: "assistant", text: "" }]);
        attachStream(res.thread_id);
      } else {
        setLoading(false);
      }
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: `错误：${e}` }]);
      setLoading(false);
    }
  }

  async function resume() {
    if (!threadId || loading) return;
    const t = input.trim();
    let marketplace: string | undefined;
    let category: string | undefined;
    if (t.startsWith("marketplace=")) {
      const [mp, cat] = t.split(",");
      marketplace = mp.split("=")[1]?.trim();
      if (cat?.includes("=")) category = cat.split("=")[1]?.trim();
    } else {
      category = t;
      marketplace = pendingInterrupt?.current?.marketplace;
    }
    setInput("");
    setMessages((m) => [...m, { role: "user", text: t }]);
    setPendingInterrupt(null);
    setLoading(true);
    try {
      // 注入补充的 marketplace/category 重跑流式图（parse_intent 用注入值覆盖）
      const res = await streamChat(t, threadId, marketplace, category);
      if (res.status === "streaming" && res.thread_id) {
        setMessages((m) => [...m, { role: "assistant", text: "" }]);
        attachStream(res.thread_id);
      } else {
        setLoading(false);
      }
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: `错误：${e}` }]);
      setLoading(false);
    }
  }

  return (
    <div className="flex h-screen bg-gradient-to-br from-slate-950 via-zinc-950 to-black text-zinc-100">
      {/* 侧边栏 */}
      <aside className="hidden w-72 flex-col border-r border-white/10 bg-white/[0.03] backdrop-blur-xl md:flex">
        <div className="flex flex-1 flex-col overflow-hidden p-4">
          <button
            onClick={async () => {
              // 当前会话已是新会话（无用户消息）→ 不再重复新建，避免空会话堆积
              if (!messages.some((m) => m.role === "user")) {
                setToast("当前已是新会话，直接输入即可");
                setTimeout(() => setToast(null), 2000);
                return;
              }
              try {
                const { session_id } = await createChatSession();
                setThreadId(session_id);
                setMessages([WELCOME]);
                setPendingInterrupt(null);
                listSessions().then(setSessions).catch(() => {});
              } catch (e) {
                setToast(e instanceof Error ? e.message : String(e));
                setTimeout(() => setToast(null), 3000);
              }
            }}
            className="mb-4 w-full rounded-lg bg-gradient-to-r from-indigo-600 to-blue-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-indigo-500/30 transition hover:from-indigo-500 hover:to-blue-500"
          >
            + 新建会话
          </button>
          {toast && (
            <div className="mb-3 animate-pulse rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              ⚠ {toast}
            </div>
          )}
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-cyan-400/60">
            会话历史
          </h2>
          <div className="flex-1 space-y-0.5 overflow-auto">
            {(() => {
              const now = new Date();
              const buckets: Record<string, ChatSession[]> = { 今天: [], 昨天: [], "7天内": [], "30天内": [], 更早: [] };
              for (const s of sessions) {
                const d = new Date(s.created_at);
                const diffDays = (now.getTime() - d.getTime()) / 86400000;
                if (d.toDateString() === now.toDateString()) buckets["今天"].push(s);
                else if (diffDays < 2) buckets["昨天"].push(s);
                else if (diffDays < 7) buckets["7天内"].push(s);
                else if (diffDays < 30) buckets["30天内"].push(s);
                else buckets["更早"].push(s);
              }
              return Object.entries(buckets).map(([label, items]) =>
                items.length > 0 ? (
                  <div key={label}>
                    <p className="mb-1 mt-3 text-[10px] font-medium uppercase tracking-wider text-zinc-500">{label}</p>
                    {items.map((s) => {
                      const isActive = threadId === s.session_id;
                      const isRenaming = renamingId === s.session_id;
                      const isConfirming = confirmDeleteId === s.session_id;
                      const itemCls = `group flex items-center gap-1 rounded-md px-3 py-2 text-sm transition ${
                        isActive
                          ? "border-l-2 border-indigo-500 bg-indigo-500/10 font-medium text-indigo-300"
                          : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
                      }`;
                      const commitRename = () => {
                        const t = renameValue.trim();
                        setRenamingId(null);
                        if (t && t !== s.title) {
                          renameSession(s.session_id, t)
                            .then(() => listSessions().then(setSessions).catch(() => {}))
                            .catch((err) => {
                              setToast(err instanceof Error ? err.message : String(err));
                              setTimeout(() => setToast(null), 2000);
                            });
                        }
                      };
                      const doDelete = () => {
                        setConfirmDeleteId(null);
                        deleteSession(s.session_id)
                          .then(() => {
                            if (threadId === s.session_id) {
                              setThreadId(null);
                              setMessages([WELCOME]);
                            }
                            listSessions().then(setSessions).catch(() => {});
                          })
                          .catch((err) => {
                            setToast(err instanceof Error ? err.message : String(err));
                            setTimeout(() => setToast(null), 2000);
                          });
                      };
                      return (
                        <div key={s.session_id} className={itemCls}>
                          {isRenaming ? (
                            <input
                              autoFocus
                              value={renameValue}
                              onChange={(e) => setRenameValue(e.target.value)}
                              onBlur={commitRename}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") commitRename();
                                if (e.key === "Escape") setRenamingId(null);
                              }}
                              className="flex-1 rounded border border-indigo-500/40 bg-black/40 px-2 py-0.5 text-sm text-white outline-none"
                            />
                          ) : isConfirming ? (
                            <div className="flex flex-1 items-center justify-between gap-2">
                              <span className="truncate text-red-300">删除此会话？</span>
                              <span className="flex shrink-0 gap-2">
                                <button
                                  onClick={doDelete}
                                  className="rounded bg-red-600/80 px-2 py-0.5 text-xs text-white hover:bg-red-500"
                                >
                                  删除
                                </button>
                                <button
                                  onClick={() => setConfirmDeleteId(null)}
                                  className="text-xs text-zinc-400 hover:text-zinc-200"
                                >
                                  取消
                                </button>
                              </span>
                            </div>
                          ) : (
                            <>
                              <button
                                onClick={() => loadSession(s.session_id)}
                                className="flex-1 truncate text-left"
                              >
                                {s.title || "新会话"}
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setRenamingId(s.session_id);
                                  setRenameValue(s.title || "");
                                }}
                                className="hidden shrink-0 text-xs text-zinc-500 hover:text-cyan-400 group-hover:inline"
                                title="重命名"
                              >
                                ✏
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setConfirmDeleteId(s.session_id);
                                }}
                                className="hidden shrink-0 text-xs text-zinc-500 hover:text-red-400 group-hover:inline"
                                title="删除"
                              >
                                🗑
                              </button>
                            </>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : null,
              );
            })()}
            {sessions.length === 0 && <p className="py-4 text-center text-xs text-zinc-600">暂无历史会话</p>}
          </div>
        </div>
        {user && (
          <div className="border-t border-white/10 p-4">
            <p className="text-sm font-medium text-zinc-300">
              {user.username}
              <span className="ml-2 rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] text-indigo-300">
                {user.is_super_admin ? "超管" : user.plan === "basic" ? "初级" : "免费"}
              </span>
            </p>
            {user.quota && !user.quota.unlimited && user.quota.total !== null && (
              <p className="mt-1 text-xs text-zinc-500">
                本月卖家额度：{user.quota.used} / {user.quota.total}
              </p>
            )}
            {user.quota?.unlimited && (
              <p className="mt-1 text-xs text-zinc-500">额度：不限</p>
            )}
            <button
              onClick={() => { clearToken(); window.location.href = "/"; }}
              className="mt-2 text-xs text-zinc-500 transition hover:text-red-400"
            >
              退出登录
            </button>
          </div>
        )}
      </aside>

      {/* 聊天区 */}
      <main className="flex flex-1 flex-col">
        <div className="flex-1 space-y-6 overflow-auto p-6">
          {messages.filter((m, i) => {
            // 过滤空历史消息（text 为空且无 sellers/progress 且非当前 loading 占位）
            const isLast = i === messages.length - 1;
            const isEmpty = !m.text && (!m.sellers || m.sellers.length === 0) && (!m.progress || m.progress.length === 0);
            return !(isEmpty && !(isLast && loading));
          }).map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div className="max-w-3xl">
                <div
                  className={`rounded-2xl px-4 py-3 ${
                    m.role === "user"
                      ? "bg-gradient-to-r from-indigo-600 to-blue-600 text-white shadow-lg shadow-indigo-500/20"
                      : "border border-white/10 bg-white/[0.05] backdrop-blur-sm"
                  }`}
                >
                  {m.progress && m.progress.length > 0 && (
                    <details className="mb-2 group" open={!m.sellers || m.sellers.length === 0}>
                      <summary className="flex cursor-pointer items-center gap-2 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-1.5 text-xs text-zinc-500 transition hover:bg-white/[0.05]">
                        <span className="text-[10px] transition group-open:rotate-90">▶</span>
                        <span className="flex items-center gap-1">
                          <span className="text-indigo-400">🧠</span>
                          思考过程
                        </span>
                      </summary>
                      <div className="mt-1.5 space-y-1 border-b border-white/5 pb-2">
                        {m.progress.map((p, pi) => (
                          <div key={pi} className="flex items-center gap-2 text-xs">
                            <span className={p.status === "done" ? "text-green-400" : "animate-pulse text-indigo-400"}>
                              {p.status === "done" ? "✓" : "⚙"}
                            </span>
                            <span className={p.status === "done" ? "text-zinc-400" : "text-zinc-300"}>{p.message}</span>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                  {m.text && (
                    <div className={`prose-sm text-sm leading-relaxed ${m.role === "user" ? "" : "text-zinc-200"}`}>
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                          strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
                          ul: ({ children }) => <ul className="mb-2 list-disc pl-4 last:mb-0">{children}</ul>,
                          ol: ({ children }) => <ol className="mb-2 list-decimal pl-4 last:mb-0">{children}</ol>,
                          li: ({ children }) => <li className="mb-0.5">{children}</li>,
                          code: ({ children }) => (
                            <code className="rounded bg-white/10 px-1 py-0.5 text-xs text-cyan-300">{children}</code>
                          ),
                          a: ({ href, children }) => (
                            <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">{children}</a>
                          ),
                          table: ({ children }) => <table className="my-2 w-full border-collapse text-xs">{children}</table>,
                          th: ({ children }) => <th className="border border-white/10 bg-white/[0.03] px-2 py-1 text-left text-cyan-400/80">{children}</th>,
                          td: ({ children }) => <td className="border border-white/5 px-2 py-1">{children}</td>,
                          h1: ({ children }) => <h1 className="mb-2 text-base font-bold text-white">{children}</h1>,
                          h2: ({ children }) => <h2 className="mb-2 text-sm font-bold text-white">{children}</h2>,
                          h3: ({ children }) => <h3 className="mb-1 text-sm font-semibold text-white">{children}</h3>,
                        }}
                      >
                        {m.text}
                      </ReactMarkdown>
                    </div>
                  )}
                  {!m.text && loading && (
                    <div className="flex items-center gap-1.5 py-1">
                      <span className="h-2 w-2 animate-bounce rounded-full bg-indigo-400" style={{ animationDelay: "-0.3s" }} />
                      <span className="h-2 w-2 animate-bounce rounded-full bg-indigo-400" style={{ animationDelay: "-0.15s" }} />
                      <span className="h-2 w-2 animate-bounce rounded-full bg-indigo-400" />
                    </div>
                  )}
                </div>
                {m.quota && (m.quota.exhausted || m.quota.upgrade_available) && !m.quota.unlimited && (
                  <div className={`mt-3 rounded-xl border px-4 py-3 text-sm ${
                    m.quota.exhausted
                      ? "border-red-500/30 bg-red-500/10 text-red-300"
                      : "border-yellow-500/30 bg-yellow-500/10 text-yellow-300"
                  }`}>
                    {m.quota.exhausted
                      ? "⛔ 本月免费额度已用完"
                      : `⚠ 还有 ${m.quota.new_skipped ?? 0} 个卖家因额度未展示`}
                    ，升级初级用户每月可获取 200 个，请联系管理员开通。
                  </div>
                )}
                {m.sellers && m.sellers.length > 0 && (
                  <SellerTable sellers={m.sellers} />
                )}
              </div>
            </div>
          ))}
          {loading && messages.length > 0 && !messages[messages.length - 1]?.text && (
            <div className="flex items-center gap-2 text-sm text-indigo-400">
              <span className="h-2 w-2 animate-ping rounded-full bg-indigo-400" />
              分析中…
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* 输入区 */}
        <div className="border-t border-white/10 bg-white/[0.02] p-4 backdrop-blur-sm">
          {pendingInterrupt ? (
            <div className="mx-auto flex max-w-3xl gap-3">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="输入品类（如 outdoor furniture），市场用默认或输入 marketplace=amazon.co.uk"
                className="flex-1 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none transition focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/20"
                onKeyDown={(e) => e.key === "Enter" && resume()}
              />
              <button
                onClick={resume}
                className="rounded-xl bg-gradient-to-r from-indigo-600 to-blue-600 px-6 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/30 transition hover:from-indigo-500 hover:to-blue-500"
              >
                提交补充
              </button>
            </div>
          ) : (
            <div className="mx-auto flex max-w-3xl gap-3">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="美国站卖杯子的2个中国卖家"
                className="flex-1 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2.5 text-sm text-white placeholder-zinc-500 outline-none transition focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/20"
                onKeyDown={(e) => e.key === "Enter" && (loading ? pause() : send())}
              />
              <button
                onClick={loading ? pause : send}
                className={loading ? "rounded-xl bg-red-600 px-6 py-2.5 text-sm font-medium text-white shadow-lg shadow-red-500/30 transition hover:bg-red-500" : "rounded-xl bg-gradient-to-r from-indigo-600 to-blue-600 px-6 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/30 transition hover:from-indigo-500 hover:to-blue-500"}
              >
                {loading ? "⏸ 暂停" : "发送"}
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
