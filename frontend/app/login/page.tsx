"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { login } from "@/lib/api";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      await login(username, password);
      router.push("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    }
    setLoading(false);
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-950 via-zinc-950 to-black p-8">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-3xl font-bold text-transparent">
            获客 Agent
          </h1>
          <p className="mt-2 text-sm text-zinc-500">亚马逊卖家智能获取系统</p>
        </div>
        <form onSubmit={onSubmit} className="space-y-4 rounded-2xl border border-white/10 bg-white/[0.03] p-8 backdrop-blur-xl">
          <input
            className="w-full rounded-xl border border-white/10 bg-white/[0.05] px-4 py-3 text-sm text-white placeholder-zinc-500 outline-none transition focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/20"
            placeholder="用户名"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          <input
            type="password"
            className="w-full rounded-xl border border-white/10 bg-white/[0.05] px-4 py-3 text-sm text-white placeholder-zinc-500 outline-none transition focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/20"
            placeholder="密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-xl bg-gradient-to-r from-indigo-600 to-blue-600 py-3 text-sm font-medium text-white shadow-lg shadow-indigo-500/30 transition hover:from-indigo-500 hover:to-blue-500 disabled:opacity-50"
          >
            {loading ? "登录中…" : "登录"}
          </button>
        </form>
        {error && <p className="mt-4 text-center text-sm text-red-400">{error}</p>}
        <p className="mt-6 text-center text-sm text-zinc-500">
          没有账号？<Link href="/register" className="text-cyan-400 transition hover:text-cyan-300">注册</Link>
        </p>
      </div>
    </main>
  );
}
