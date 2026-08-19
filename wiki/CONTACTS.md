# 亚马逊卖家联系方式获取方案

> 目标：尽可能拿到卖家的 **email / 电话**，供货代触达。核心矛盾：**亚马逊刻意不公开卖家 email**，只能从多源拼凑。

---

## 现状：当前系统能拿到什么

| 来源 | 拿到 | 状态 |
|---|---|---|
| junglee seller 对象 | **phone**（业务电话）、businessName、address | ✅ 已接入 → contact 表 |
| amazon-sellers-scraper | businessAddress、businessName | ✅ 已接入（enrich） |
| website_provider（自写） | 官网 email/phone（品牌名→搜官网→抓 about） | ⚠️ 已写，命中低（多数官网用 contact form） |
| amazon 卖家 email | — | ❌ 平台不公开 |

**结论**：phone 靠 junglee（amazon 合规公开业务电话）较可靠；**email 几乎拿不到**（amazon 不给 + 官网多 contact form）。

---

## 获取途径（按 命中率 / 成本 / 合规）

### 1. Amazon 公开业务信息（合规，已接入）
- junglee `seller.phone` + `businessAddress`（amazon 合规要求卖家公开业务信息，尤其 US marketplace）
- **命中**：phone 较高；无 email
- **成本**：含在 junglee 调用
- **合规**：公开数据 ✓

### 2. 卖家官网（httpx，已写 website_provider）
- 品牌名 → DuckDuckGo 搜官网 → 抓 about/contact 页 → 正则提 email/phone
- **命中**：email ~20%（contact form 多），phone 较高
- **成本**：免费
- **合规**：公开网页 ✓
- **限制**：contact form / JS 渲染 / 图片 email；DDG 搜官网偶发限速
- **可改进**：加 `mailto:` 解析、JSON-LD（schema.org ContactPoint）、常见 contact 路径

### 3. Email 查找 API（付费，高命中）★ 推荐
- **Hunter.io / Snov.io / Apollo.io**：给官网域名 → 查 email（验证有效 + 推断格式如 `first@brand.com`）
- **命中**：**70–90%**（含验证，知道 email 真实可用）
- **成本**：付费（Hunter $34/月起 500 次；Snov $39/月；Apollo $49/月含其他数据）
- **合规**：email 来自公开网络抓取，但**触达必须 opt-out**（CAN-SPAM/GDPR）
- **适合**：要高 email 覆盖——这是补 email 的关键
- **实现**：API key 配置 + provider（域名 → email 列表 + 验证状态）

### 4. 企业工商数据（中国卖家尤其有效）
- **国内**：企查查 / 天眼查 API → 公司名 → 电话/邮箱/法人
- **海外**：OpenCorporates（公司主体 → 注册信息）
- **命中**：中（中国卖家有工商登记电话）
- **成本**：企查查/天眼查付费 API
- **合规**：企业公开信息 ✓
- **适合**：**中国发货的亚马逊卖家**（货代核心客户，工商电话可联）

### 5. LinkedIn（找对接人）
- 品牌/公司 → LinkedIn 找采购负责人/老板 → 联系方式
- **命中**：中（找对人，但联系方式要 Premium/Sales Navigator）
- **成本**：LinkedIn Sales Navigator 贵 + 反爬
- **合规**：**个人数据，GDPR 风险高**，需明确授权场景
- **限制**：反爬严，需专用工具（Apify 有 LinkedIn scraper 但合规敏感）

### 6. 海关提单数据
- 进出口提单（含真实贸易的进出口商 + 联系方式）
- **命中**：高（真实进出口记录的联系方式）
- **成本**：付费数据服务商（如 ImportGenius、Panjiva；国内海关数据商）
- **合规**：提单数据合法但**敏感**，需确认数据源合法
- **适合**：找**有真实对美出口**的卖家（货代精准客户）

---

## 推荐方案（分层，按 ROI）

**第一层（已做，免费）—— 基础覆盖**
- junglee `seller.phone`（amazon 公开）→ 已入 contact
- website_provider 官网 email/phone → 已写（集成到 enrich 即可启用）
- → phone 基本覆盖，email 命中低

**第二层（推荐加，付费但高 ROI）—— 补 email**
- **Email 查找 API（Hunter/Snov）**：给官网域名查 email，命中 70–90%
- 把 website_provider 找到的官网域名喂 Hunter → 拿验证过的 email
- phone + email 双覆盖，触达率大幅提升

**第三层（可选，特定场景）**
- 中国卖家多 → **工商数据**（企查查/天眼查，工商电话）
- 要找对接人 → **LinkedIn**（Sales Navigator，合规慎重）
- 要真实贸易客户 → **海关数据**（提单联系方式）

---

## 取舍总表

| 途径 | email 命中 | phone 命中 | 成本 | 合规 | 推荐度 |
|---|---|---|---|---|---|
| Amazon 公开（junglee） | 无 | 高 | 含 junglee | ✓ | 已接入 |
| 官网（website_provider） | ~20% | 中 | 免费 | ✓ | 已写，启用 |
| **Email API（Hunter/Snov）** | **70–90%** | — | 付费 | 触达需 opt-out | ★★★ 强推 |
| 工商（企查查/天眼查） | 中 | 中 | 付费 | ✓ | 中国卖家 ★★ |
| LinkedIn | 中 | — | 贵 | 个人数据⚠️ | 对接人 ★ |
| 海关提单 | 高 | 高 | 付费/敏感 | 敏感 | 真实贸易 ★★ |

---

## 实现思路

### Phase 1（已完成）：amazon phone + 官网 email
- junglee `seller.phone` → contact 表（save_node 已写）
- website_provider（品牌→官网→email/phone）→ 集成到 enrich（调 fetch_website_contacts）

### Phase 2（推荐下一步）：Email 查找 API
- 新 `app/providers/email_finder.py`（Hunter 或 Snov）
  - 输入：官网域名（website_provider 找到的，或 junglee brand 推断）
  - 输出：`[{email, confidence, verified}]`
  - 集成：enrich 或新节点，对每个 lead 调（限 lead 数 + 缓存域名）
- 配置：`EMAIL_FINDER_API_KEY`（.env）
- 成本控制：缓存（同域名不重复查）+ 只对 top leads（recommend 档）

### Phase 3（可选）：工商 / LinkedIn / 海关
- 按业务需要选（中国卖家 / 对接人 / 真实贸易）

---

## 为什么 email 这么难（根因）

亚马逊**刻意隐藏卖家 email**——防止买家绕开平台联系卖家（保护交易抽成）。所以：
- amazon 页面**没有**卖家 email
- 只有「Contact seller」站内信（amazon 中转，不暴露 email）
- 卖家业务电话/地址是**合规公开**的（部分国家要求），所以 phone 能拿

**email 只能从 amazon 之外补**（官网 / email API / 工商 / LinkedIn）——这是行业普遍难题，非本系统独有。

---

## 建议的下一步

1. **启用 website_provider**（已写，集成到 enrich）—— 免费先拿到一批 email/phone
2. **接 Hunter 或 Snov**（Phase 2）—— 付费但 email 命中 70–90%，触达率质变
3. 按客户构成选第三层（中国卖家多 → 工商）

当前 Apify 充值后，phone（junglee）会自动来；email 要靠 Phase 2 的 email API 才有实质覆盖。
