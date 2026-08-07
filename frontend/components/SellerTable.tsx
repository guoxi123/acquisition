"use client";

import Papa from "papaparse";
import { useState } from "react";

import type { Seller } from "@/lib/api";

const PAGE_SIZE = 10;

// 聊天消息里的卖家表格：每页 10 条分页 + 导出全部 CSV。
export default function SellerTable({ sellers }: { sellers: Seller[] }) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(sellers.length / PAGE_SIZE));
  const rows = sellers.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  function exportCSV() {
    const data = sellers.map((s) => ({
      卖家ID: s.seller_id,
      名称: s.name ?? "",
      国家: s.business_country ?? "",
      Feedback: s.total_feedback ?? "",
      注册时间: s.member_since ?? "",
      综合评分: s.seller_score ?? "",
    }));
    const csv = Papa.unparse(data);
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "sellers.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <details className="mt-3 group" open>
      <summary className="flex cursor-pointer items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-4 py-2 text-sm text-zinc-400 transition hover:bg-white/[0.06]">
        <span className="text-xs transition group-open:rotate-90">▶</span>
        查看 {sellers.length} 个卖家
      </summary>
      <div className="mt-2 overflow-hidden rounded-xl border border-white/10 bg-white/[0.02]">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 bg-white/[0.03]">
                {["卖家", "国家", "Feedback", "联系方式", "评分"].map((h) => (
                  <th key={h} className="px-4 py-2.5 text-left font-medium text-cyan-400/80">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.seller_id} className="border-b border-white/5 transition hover:bg-white/[0.04]">
                  <td className="px-4 py-2.5">
                    <a
                      href={`https://www.amazon.com/sp?seller=${s.seller_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-400 transition hover:text-blue-300 hover:underline"
                    >
                      {s.name ?? s.seller_id}
                    </a>
                  </td>
                  <td className="px-4 py-2.5">
                    {s.business_country ? (
                      <span className="rounded-md bg-white/10 px-2 py-0.5 text-xs text-zinc-300">{s.business_country}</span>
                    ) : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-zinc-400">{s.total_feedback ?? "—"}</td>
                  <td className="px-4 py-2.5">
                    {s.contacts && s.contacts.length > 0 ? (
                      <div className="space-y-1">
                        {s.contacts.map((c, i) => (
                          <div key={i} className="whitespace-nowrap text-xs">
                            <span className="text-zinc-300">{c.type === "phone" ? "📞" : "✉"} {c.value}</span>
                            <span className="ml-1 rounded bg-indigo-500/20 px-1 py-0.5 text-[10px] text-indigo-300">{c.source}</span>
                          </div>
                        ))}
                      </div>
                    ) : "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className={`font-semibold ${s.seller_score && s.seller_score >= 70 ? "text-green-400" : s.seller_score && s.seller_score >= 40 ? "text-yellow-400" : "text-zinc-500"}`}>
                      {s.seller_score ?? "—"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 p-3">
          {pages > 1 && (
            <div className="flex items-center gap-3 text-xs text-zinc-400">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="rounded border border-white/10 px-2 py-1 transition hover:bg-white/5 disabled:opacity-30"
              >
                ‹ 上一页
              </button>
              <span>第 {page + 1} / {pages} 页 · 每页 {PAGE_SIZE}</span>
              <button
                onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
                disabled={page >= pages - 1}
                className="rounded border border-white/10 px-2 py-1 transition hover:bg-white/5 disabled:opacity-30"
              >
                下一页 ›
              </button>
            </div>
          )}
          <button
            onClick={exportCSV}
            className="rounded-lg border border-white/20 px-4 py-1.5 text-sm text-cyan-400 transition hover:bg-white/5"
          >
            📥 导出 CSV
          </button>
        </div>
      </div>
    </details>
  );
}
