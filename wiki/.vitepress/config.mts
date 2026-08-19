import { createRequire } from "module";
import { defineConfig } from "vitepress";

const require = createRequire(import.meta.url);

const sidebar = [
  {
    text: "产品与方案总览",
    items: [
      { text: "整体技术方案", link: "/PRODUCT_FLOW" },
      { text: "系统流程：从输入到输出", link: "/FLOW" },
      { text: "使用文档", link: "/USAGE" },
    ],
  },
  {
    text: "Agent 架构设计",
    items: [
      { text: "Router 模式：请求分流", link: "/router-pattern" },
      { text: "结构化意图提取", link: "/structured-intent-extraction" },
      { text: "手写 LangGraph ReAct 子图", link: "/handwritten-react-subgraph" },
      { text: "采集循环图", link: "/collect-loop-graph" },
      { text: "Skill/Tool 注册表", link: "/skill-tool-registry" },
      { text: "数量 + 配额取最小", link: "/desired-count-quota" },
      { text: "两级会话压缩", link: "/memory-two-level-compression" },
      { text: "轻量 HITL", link: "/lightweight-hitl" },
      { text: "DeepSeek function calling", link: "/deepseek-function-calling" },
      { text: "RAG 语义检索", link: "/rag-semantic-search" },
    ],
  },
  {
    text: "数据采集",
    items: [
      { text: "Apify Actor 接口", link: "/ACTOR" },
      { text: "联系方式获取方案", link: "/CONTACTS" },
      { text: "批量采集降本", link: "/apify-batch-scrape" },
    ],
  },
  {
    text: "流式与前端",
    items: [
      { text: "大模型流式输出方案", link: "/STREAMING" },
      { text: "SSE 对话（后端）", link: "/sse-streaming-chat" },
      { text: "流式前端状态机", link: "/nextjs-streaming-frontend" },
      { text: "HITL 前端 UI", link: "/hitl-frontend-ui" },
    ],
  },
  {
    text: "后端工程",
    items: [
      { text: "SaaS 配额设计", link: "/saas-quota-design" },
      { text: "错误分类与重试", link: "/error-retry-classification" },
      { text: "阿里云 PNVS 短信", link: "/aliyun-pnvs-sms" },
      { text: "多 Worker 幽灵取消", link: "/multi-worker-ghost-cancel" },
    ],
  },
  {
    text: "测试与可观测",
    items: [
      { text: "测试与评估体系", link: "/testing-and-eval" },
      { text: "Agent eval 测试集", link: "/agent-eval" },
      { text: "低资源可观测", link: "/low-resource-observability" },
    ],
  },
  {
    text: "部署运维",
    items: [
      { text: "Compose 生产部署", link: "/compose-prod-deploy" },
      { text: "Alembic 版本表踩坑", link: "/alembic-dirty-version" },
      { text: "PG 数据同步踩坑", link: "/pg-data-sync-pitfalls" },
    ],
  },
];

export default defineConfig({
  title: "获客 Agent 技术文档",
  description: "亚马逊卖家获客 Agent 的技术方案与踩坑记录",
  srcDir: "./",
  outDir: "../wiki-dist",
  base: "/wiki/",
  cleanUrls: true,
  // md 在 ./（wiki/node_modules 之外），Rollup 从 md 虚拟模块向上解析 vue
  // 永远走不到 wiki/node_modules；显式 alias 到真实路径
  vite: {
    resolve: {
      alias: {
        "vue/server-renderer": require.resolve("vue/server-renderer"),
        vue: require.resolve("vue"),
      },
    },
  },
  // 文档里引用的仓库相对路径（./../backend/…、./../CLAUDE 等）和 localhost 示例不是站内链接
  ignoreDeadLinks: [/^\.\//, /^http:\/\/localhost/],
  themeConfig: {
    nav: [
      { text: "首页", link: "/" },
      { text: "使用文档", link: "/USAGE" },
    ],
    sidebar,
    search: {
      provider: "local",
      options: {
        translations: {
          button: { buttonText: "搜索文档", buttonAriaLabel: "搜索文档" },
          modal: {
            noResultsText: "没有找到结果",
            resetButtonTitle: "清除查询",
            footer: { selectText: "选择", navigateText: "切换", closeText: "关闭" },
          },
        },
      },
    },
    outline: { label: "本页目录", level: [2, 3] },
    docFooter: { prev: "上一篇", next: "下一篇" },
    lastUpdated: { text: "最后更新" },
    returnToTop: { label: "回到顶部" },
  },
});
