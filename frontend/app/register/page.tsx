"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { register, sendSms } from "@/lib/api";

const PHONE_RE = /^1[3-9]\d{9}$/;

export default function RegisterPage() {
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const router = useRouter();

  useEffect(
    () => () => {
      if (timerRef.current) clearInterval(timerRef.current);
    },
    [],
  );

  function startCountdown() {
    setCountdown(60);
    timerRef.current = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          return 0;
        }
        return c - 1;
      });
    }, 1000);
  }

  async function onSendCode(e: React.MouseEvent) {
    e.preventDefault();
    setError("");
    if (!PHONE_RE.test(phone)) {
      setError("请输入正确的手机号");
      return;
    }
    setSending(true);
    try {
      await sendSms(phone);
      startCountdown();
    } catch (err) {
      setError(err instanceof Error ? err.message : "验证码发送失败");
    }
    setSending(false);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!PHONE_RE.test(phone)) {
      setError("请输入正确的手机号");
      return;
    }
    if (!code.trim()) {
      setError("请输入验证码");
      return;
    }
    if (!password) {
      setError("请设置密码");
      return;
    }
    setLoading(true);
    try {
      await register(phone, code, password);
      router.push("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败");
    }
    setLoading(false);
  }

  const phoneValid = PHONE_RE.test(phone);

  return (
    <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-950 via-zinc-950 to-black p-8">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-3xl font-bold text-transparent">
            注册账号
          </h1>
          <p className="mt-2 text-sm text-zinc-500">手机号注册 · 短信验证码校验</p>
        </div>
        <form
          onSubmit={onSubmit}
          className="space-y-4 rounded-2xl border border-white/10 bg-white/[0.03] p-8 backdrop-blur-xl"
        >
          <input
            className="w-full rounded-xl border border-white/10 bg-white/[0.05] px-4 py-3 text-sm text-white placeholder-zinc-500 outline-none transition focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/20"
            placeholder="手机号"
            value={phone}
            onChange={(e) => setPhone(e.target.value.replace(/\D/g, "").slice(0, 11))}
            required
          />
          <div className="flex gap-2">
            <input
              className="flex-1 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-3 text-sm text-white placeholder-zinc-500 outline-none transition focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/20"
              placeholder="短信验证码"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              required
            />
            <button
              type="button"
              onClick={onSendCode}
              disabled={!phoneValid || sending || countdown > 0}
              className="shrink-0 rounded-xl border border-indigo-500/40 px-4 py-3 text-sm text-indigo-300 transition hover:bg-indigo-500/10 disabled:opacity-40"
            >
              {countdown > 0 ? `${countdown}s` : "获取验证码"}
            </button>
          </div>
          <input
            type="password"
            className="w-full rounded-xl border border-white/10 bg-white/[0.05] px-4 py-3 text-sm text-white placeholder-zinc-500 outline-none transition focus:border-indigo-500/50 focus:ring-2 focus:ring-indigo-500/20"
            placeholder="设置密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-xl bg-gradient-to-r from-indigo-600 to-blue-600 py-3 text-sm font-medium text-white shadow-lg shadow-indigo-500/30 transition hover:from-indigo-500 hover:to-blue-500 disabled:opacity-50"
          >
            {loading ? "注册中…" : "注册"}
          </button>
        </form>
        {error && <p className="mt-4 text-center text-sm text-red-400">{error}</p>}
        <p className="mt-6 text-center text-sm text-zinc-500">
          已有账号？
          <Link href="/login" className="text-cyan-400 transition hover:text-cyan-300">
            登录
          </Link>
        </p>
      </div>
    </main>
  );
}
