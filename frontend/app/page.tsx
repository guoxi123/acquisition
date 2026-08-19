"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { clearToken, getToken } from "@/lib/api";

export default function HomePage() {
  const [loggedIn, setLoggedIn] = useState(false);

  useEffect(() => {
    setLoggedIn(!!getToken());
  }, []);

  const features = [
    {
      icon: "🔍",
      title: "智能意图识别",
      desc: "一句话识别目标市场、品类、筛选条件，信息不全时自动追问补充",
    },
    {
      icon: "🛒",
      title: "全球卖家挖掘",
      desc: "对接 Amazon 16 个站点，批量抓取品类头部卖家及产品数据",
    },
    {
      icon: "🏭",
      title: "中国供应链识别",
      desc: "自动识别卖家国籍，优先锁定中国卖家，直击货代核心目标",
    },
    {
      icon: "📞",
      title: "联系方式挖掘",
      desc: "自动匹配企业电话、邮箱等联系方式，直接触达决策人",
    },
    {
      icon: "🤖",
      title: "AI 智能评分",
      desc: "基于反馈量、活跃度、规模等多维度自动评分排序，优先推荐高潜客户",
    },
    {
      icon: "💾",
      title: "数据去重复用",
      desc: "多层缓存策略，7-14 天内同品类查询直接复用，省时间省费用",
    },
  ];

  const steps = [
    {
      step: "01",
      title: "描述需求",
      desc: '输入如"美国站户外家具的 FBA 卖家"，AI 自动解析查询意图',
    },
    {
      step: "02",
      title: "AI 采集分析",
      desc: "Agent 自动抓取 Amazon 数据，识别中国卖家，挖掘联系方式",
    },
    {
      step: "03",
      title: "获取结果",
      desc: "按 AI 评分排序，一键导出 CSV，直接对接销售团队跟进",
    },
  ];

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-zinc-950 to-black text-zinc-100">
      {/* Nav */}
      <nav className="sticky top-0 z-50 border-b border-white/10 bg-black/30 backdrop-blur-xl">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-cyan-500 text-white shadow-lg shadow-indigo-500/30">
              <span className="text-sm font-bold">A</span>
            </div>
            <span className="text-lg font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent">
              获客 Agent
            </span>
          </div>
          <div className="flex items-center gap-3">
            <a
              href="/wiki/"
              className="rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-300 transition hover:bg-white/5"
            >
              文档
            </a>
            <Link
              href="/login"
              className="rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-300 transition hover:bg-white/5"
            >
              登录
            </Link>
            <Link
              href={loggedIn ? "/chat" : "/login"}
              className="rounded-lg bg-gradient-to-r from-indigo-600 to-blue-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-indigo-500/30 transition hover:from-indigo-500 hover:to-blue-500"
            >
              {loggedIn ? "进入工作台" : "免费开始"}
            </Link>
            {loggedIn && (
              <button
                onClick={() => {
                  clearToken();
                  setLoggedIn(false);
                  window.location.href = "/";
                }}
                className="text-xs text-zinc-500 transition hover:text-red-400"
              >
                退出
              </button>
            )}
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div
          className="absolute inset-0 opacity-30"
          style={{
            backgroundImage:
              "radial-gradient(circle at 20% 20%, rgba(99,102,241,0.25), transparent 50%), radial-gradient(circle at 80% 60%, rgba(34,211,238,0.2), transparent 50%)",
          }}
        />
        <div className="relative mx-auto max-w-6xl px-6 py-24 text-center">
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-indigo-500/20 bg-indigo-500/10 px-4 py-1.5 text-xs text-indigo-300">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-400" />
            面向国际货代行业的 AI 获客工具
          </div>
          <h1 className="mx-auto max-w-4xl text-5xl font-bold leading-tight tracking-tight md:text-6xl">
            10 秒锁定
            <span className="bg-gradient-to-r from-indigo-400 via-purple-400 to-cyan-400 bg-clip-text text-transparent">
              {" "}亚马逊中国卖家
            </span>
            <br />
            从大海捞针到精准触达
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg text-zinc-400">
            传统方式找一个亚马逊卖家联系方式需要 30 分钟，现在只需要一句话。AI
            Agent 自动采集、识别、评分、挖掘联系方式，让你的销售团队专注在谈单上。
          </p>
          <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Link
              href={loggedIn ? "/chat" : "/login"}
              className="group rounded-xl bg-gradient-to-r from-indigo-600 to-blue-600 px-8 py-4 text-base font-medium text-white shadow-2xl shadow-indigo-500/30 transition hover:from-indigo-500 hover:to-blue-500 hover:shadow-indigo-500/40"
            >
              立即开始 →
              <span className="ml-2 inline-block transition group-hover:translate-x-1">
                →
              </span>
            </Link>
            <Link
              href="#features"
              className="rounded-xl border border-white/10 bg-white/[0.03] px-8 py-4 text-base font-medium text-zinc-300 backdrop-blur transition hover:bg-white/[0.06]"
            >
              查看功能
            </Link>
          </div>

          {/* Stats */}
          <div className="mx-auto mt-20 grid max-w-3xl grid-cols-3 gap-6">
            {[
              { num: "16", label: "Amazon 站点覆盖" },
              { num: "10s", label: "单轮查询耗时" },
              { num: "99%", label: "中国卖家识别率" },
            ].map((s) => (
              <div
                key={s.label}
                className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 backdrop-blur"
              >
                <div className="text-3xl font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent">
                  {s.num}
                </div>
                <div className="mt-1 text-sm text-zinc-500">{s.label}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Problem */}
      <section className="mx-auto max-w-6xl px-6 py-20">
        <div className="rounded-3xl border border-red-500/20 bg-gradient-to-br from-red-500/[0.05] to-transparent p-10">
          <div className="mb-8 text-center">
            <div className="mb-3 inline-block rounded-lg bg-red-500/20 px-3 py-1 text-xs text-red-400">
              你是否遇到过这些问题？
            </div>
            <h2 className="text-3xl font-bold text-white">
              传统货代获客 = 低效 × 重复劳动
            </h2>
          </div>
          <div className="grid gap-6 md:grid-cols-3">
            {[
              {
                title: "手动找卖家太慢",
                desc: "手动翻 Amazon 一页页找卖家，一天找不到 10 个有效客户，还容易漏过优质目标",
              },
              {
                title: "联系方式难获取",
                desc: "找到卖家后，还要一个个搜联系方式，至少 30 分钟/客户，效率极低",
              },
              {
                title: "团队重复劳动",
                desc: "销售 A 今天搜过的品类，销售 B 明天又搜一遍，资源浪费严重",
              },
            ].map((p) => (
              <div
                key={p.title}
                className="rounded-2xl border border-white/5 bg-white/[0.02] p-6"
              >
                <div className="mb-3 text-2xl">⚠️</div>
                <h3 className="mb-2 text-lg font-semibold text-red-300">
                  {p.title}
                </h3>
                <p className="text-sm text-zinc-400 leading-relaxed">{p.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="mx-auto max-w-6xl px-6 py-20">
        <div className="mb-14 text-center">
          <div className="mb-3 inline-block rounded-lg bg-cyan-500/20 px-3 py-1 text-xs text-cyan-400">
            核心功能
          </div>
          <h2 className="text-3xl font-bold text-white">
            用 AI Agent 重构货代获客流程
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-zinc-400">
            从需求描述到精准客户列表，一站式搞定
          </p>
        </div>
        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
          {features.map((f) => (
            <div
              key={f.title}
              className="group rounded-2xl border border-white/10 bg-white/[0.03] p-6 backdrop-blur transition hover:border-indigo-500/30 hover:bg-white/[0.05]"
            >
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500/20 to-cyan-500/20 text-2xl transition group-hover:scale-110">
                {f.icon}
              </div>
              <h3 className="mb-2 text-lg font-semibold text-white">{f.title}</h3>
              <p className="text-sm text-zinc-400 leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section className="mx-auto max-w-6xl px-6 py-20">
        <div className="mb-14 text-center">
          <div className="mb-3 inline-block rounded-lg bg-purple-500/20 px-3 py-1 text-xs text-purple-400">
            使用流程
          </div>
          <h2 className="text-3xl font-bold text-white">三步完成获客</h2>
        </div>
        <div className="grid gap-8 md:grid-cols-3">
          {steps.map((s, i) => (
            <div key={s.step} className="relative">
              <div className="absolute -top-2 -left-2 text-8xl font-bold text-white/5">
                {s.step}
              </div>
              <div className="relative rounded-2xl border border-white/10 bg-white/[0.03] p-8 backdrop-blur">
                <h3 className="mb-3 text-xl font-semibold text-white">
                  {s.title}
                </h3>
                <p className="text-sm text-zinc-400 leading-relaxed">{s.desc}</p>
              </div>
              {i < steps.length - 1 && (
                <div className="hidden absolute top-1/2 -right-4 -translate-y-1/2 text-2xl text-zinc-600 md:block">
                  →
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-6xl px-6 py-20">
        <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-indigo-600/20 via-purple-600/10 to-cyan-600/20 p-12 text-center">
          <div
            className="absolute inset-0 opacity-40"
            style={{
              backgroundImage:
                "radial-gradient(circle at 50% 50%, rgba(99,102,241,0.3), transparent 60%)",
            }}
          />
          <div className="relative">
            <h2 className="text-4xl font-bold text-white">
              让 AI 帮你做获客的"脏活累活"
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-zinc-300">
              销售团队的价值是谈下客户，而不是搜客户。把机械的搜索工作交给
              Agent，把精力留给成交。
            </p>
            <Link
              href={loggedIn ? "/chat" : "/register"}
              className="mt-8 inline-flex items-center gap-2 rounded-xl bg-white px-8 py-4 text-base font-semibold text-zinc-900 shadow-2xl transition hover:bg-zinc-100"
            >
              免费注册，立即体验
              <span>→</span>
            </Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-white/10">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-8 text-sm text-zinc-500 md:flex-row">
          <div>© 2026 获客 Agent. 用 AI 重塑货代获客流程。</div>
          <div className="flex gap-6">
            <Link href="/login" className="transition hover:text-zinc-300">
              登录
            </Link>
            <Link href="/register" className="transition hover:text-zinc-300">
              注册
            </Link>
            <Link href="/chat" className="transition hover:text-zinc-300">
              工作台
            </Link>
            <a href="/wiki/" className="transition hover:text-zinc-300">
              技术文档
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
