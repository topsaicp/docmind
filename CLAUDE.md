# CLAUDE.md — DocMind 项目开发手册

> 供 AI 助手（Claude Code）在接手后续开发任务时快速建立上下文。
> 人工阅读时请直接跳到最感兴趣的章节。

---

## 一、项目概览

**DocMind** 是一个面向学术用户的 AI 文献阅读工具，核心功能：
- PDF 上传 → 解析 → 向量化 → 知识库问答（RAG）
- 文献综述、论文撰写、PDF 精读对话
- 截图分析（GLM 视觉）、文献检索（Semantic Scholar + CrossRef）
- 学术语言优化（原"降率工具"）
- 用户系统（注册/邮箱验证/JWT）+ 三档付费套餐 + 支付宝支付

**线上地址**：https://app.topsaitech.com.cn  
**服务器**：腾讯云，`root@111.229.138.131`，宝塔面板 `:8888`  
**代码仓库**：https://github.com/topsaicp/docmind（main 分支）

---

## 二、技术栈

| 层次 | 技术 |
|------|------|
| Web 框架 | FastAPI（同步，非 async）+ Uvicorn |
| ORM | SQLAlchemy 2.x（同步 Session） |
| 主数据库 | PostgreSQL（本地，宝塔管理） |
| 向量存储 | **pgvector**（在 PostgreSQL 里的 `doc_chunks` 表，直接用 psycopg2） |
| Embedding | 硅基流动 BGE-M3（1024 维，HTTP API） |
| 主力 LLM | DeepSeek（问答/综述/写作/关键词） |
| 备用 LLM | Groq Llama 3.3（429 降级/写作备用） |
| 视觉模型 | 智谱 GLM-4V-Flash（截图分析） |
| 联网检索 | Jina Search API（可选） |
| 邮件 | Brevo HTTP API（注册验证 + 管理员通知） |
| 支付 | 支付宝电脑/手机网站支付（python-alipay-sdk） |
| 前端 | 原生 HTML + 原生 JS（无框架），PDF.js 本地化 |
| 部署 | Nginx 反向代理 → Uvicorn，Supervisor 守护进程 |
| 环境变量 | `/www/docmind/env.sh`（`source` 后启动服务） |

> ⚠️ **pgvector ≠ Chroma**：`chroma_db/` 目录是历史遗留，生产环境**不使用**，
> 向量读写全部走 `services/embedder.py` 中的 psycopg2 直连 PostgreSQL。

---

## 三、目录结构

```
kb_project/
├── main.py                   # FastAPI app 入口；init_db；注册所有路由
├── config.py                 # 所有配置（环境变量读取、PLAN_LIMITS、MODEL_ROUTES）
├── requirements.txt          # Python 依赖
├── CLAUDE.md                 # 本文件
├── MAINTENANCE.md            # 运维速查（日志、重启、数据库操作）
│
├── db/
│   └── database.py           # SQLAlchemy 模型：User、Document；init_db()
│
├── routers/
│   ├── auth.py               # /api/auth/*  注册/登录/邮箱验证/JWT/套餐激活
│   ├── query.py              # /api/ask/*   RAG 问答（流式 SSE）；统计；引用元数据
│   ├── upload.py             # /api/upload  PDF 上传 → 解析 → 入库
│   ├── reduce.py             # /api/reduce  学术语言优化（按套餐限字数）
│   ├── vision.py             # /api/analyze-image/stream  GLM 图像分析（流式）
│   └── search.py             # /api/search/literature  AI 关键词 → 外文文献检索
│
├── payment/
│   ├── router.py             # /api/pay/*  /api/leads  /api/admin/*
│   ├── models.py             # PayOrder、ActivationCode、Lead 三张表
│   ├── service.py            # activate()、redeem_code()、gen_codes()（唯一开通入口）
│   └── alipay_client.py      # AliPay SDK 封装；沙箱/正式切换
│
├── services/
│   ├── embedder.py           # pgvector CRUD + 硅基流动 BGE-M3 嵌入；search()
│   ├── retriever.py          # RAG 主逻辑：retrieve / build_context / ask()
│   ├── pdf_processor.py      # PDF 解析 + 分块（子块/父块双层）+ 章节识别
│   ├── text_reducer.py       # 学术语言优化：分块流式改写
│   └── email_sender.py       # Brevo 邮件（验证邮件 + 通知邮件）
│
└── frontend/
    ├── index.html            # 首页（Landing Page）：功能介绍 + 定价
    ├── app.html              # 工作台（主应用）：所有功能页面
    ├── pdf.min.js            # PDF.js 本地化（避免 CDN 在国内失效）
    └── pdf.worker.min.js     # PDF.js Worker 本地化
```

---

## 四、关键模块职责

### 4.1 config.py — 唯一配置中心

- **`PLAN_LIMITS`**：套餐限额表（free/plus/pro/enterprise）。
  修改套餐权限**只改这里**，`get_limits(plan)` 在全项目统一取值。
- **`MODEL_ROUTES`**：任务→模型映射（qa/multi/review/writing/cite）。
  切换模型**只改这里**，业务代码不感知具体 API。
- **`MODEL_FALLBACK`**：429 限流时自动降级的备用模型。

### 4.2 routers/auth.py — 认证与权限

- `effective_plan(user)` 是**全项目唯一**的套餐判断入口。
  所有权限门控必须调这个函数，**不得**直接读 `user.plan`。
  - admin → 强制 pro
  - 付费套餐过期 → 降回 free
  - `custom` 套餐不参与到期判断（人工管理）

### 4.3 payment/ — 支付与激活

所有开通会员权益的路径（支付宝回调、激活码、管理员手动）**最终都必须调** `service.activate()`，
绝不在其他地方直接写 `user.plan`。

支付流程：
```
前端 subscribe() → POST /api/pay/orders → 支付宝收银台
→ 支付宝 POST /api/pay/alipay/notify（异步回调，验签）
→ activate() 写库
→ 前端回跳 GET /app?pay=done → checkPendingOrder() 轮询
→ 兜底 POST /api/pay/orders/{no}/sync（主动查单）
```

### 4.4 services/embedder.py — 向量引擎

- 直接操作 PostgreSQL `doc_chunks` 表，**不经过 SQLAlchemy**（用 psycopg2）。
- 首次调用 `_ensure_table()` 自动建表建索引（幂等）。
- 嵌入模型：硅基流动 BGE-M3，1024 维，批量 16 条/请求。
- `search()` 用余弦相似度（`<=>` 运算符）检索，支持按 `doc_id` 和 `section` 过滤。

### 4.5 services/retriever.py — RAG 问答

`ask()` 是唯一的问答入口，自动识别任务类型：
- **qa**（单文档精读）→ retrieve → build_context → DeepSeek
- **multi**（多文档对比）→ retrieve_per_doc → build_multi_doc_context → DeepSeek
- **review**（文献综述）→ retrieve_per_doc_for_review → 长 prompt → DeepSeek
- **writing**（论文撰写）→ retrieve → DeepSeek
- 429 限流自动降级到 MODEL_FALLBACK，对上层透明。

### 4.6 前端三文件关系

| 文件 | 路由 | 职责 |
|------|------|------|
| `frontend/index.html` | `GET /` | 官网首页：产品介绍、定价、注册引导 |
| `frontend/app.html` | `GET /app` | 工作台：所有功能页面（SPA 风格，JS 切换） |
| `pdf.min.js` + `pdf.worker.min.js` | 静态资源 | PDF.js 本地化，app.html 直接引用 |

**前端与后端的连接**：`app.html` 中 `const API = ''`（相对路径），
所有请求走 `authFetch()` 自动携带 JWT Bearer Token。

---

## 五、套餐体系

| 套餐 | DB 值 | 价格 | 特殊说明 |
|------|-------|------|---------|
| 免费版 | `free` | 免费 | 5 PDF / 20 问答/日 |
| 基础版 | `plus` | ¥9.9/月 | 截图分析解锁，语言优化 3000 词 |
| 专业版 | `pro` | ¥19.9/月 | 无限问答，语言优化 10000 词，含全模式 |
| 机构版 | `enterprise` | 人工定价 | 同 pro 限额，500 PDF |
| 专属尊享版 | `custom` | 定制 | 人工开通，不自动到期 |

**注**：`enterprise` 是遗留字段，对外已不展示；实际销售用 `custom`（专属尊享版）替代。
前端 `planLabel()` 和 `PLAN_LIMITS` 要和 `config.py` 保持一致。

---

## 六、环境变量

生产环境写入 `/www/docmind/env.sh`，启动前 `source`。
**所有密钥只走环境变量，永远不写入代码或 Git 仓库。**

| 变量名 | 用途 |
|--------|------|
| `DATABASE_URL` | PostgreSQL 连接串（含密码，绝对不提交） |
| `SECRET_KEY` | JWT 签名密钥 |
| `ADMIN_SECRET` | 管理员接口密码 |
| `DEEPSEEK_API_KEY` | 主力 LLM |
| `GROQ_API_KEY` | 备用 LLM + 写作任务 |
| `GLM_API_KEY` | 智谱 GLM-4V-Flash 视觉分析 |
| `SILICONFLOW_API_KEY` | 硅基流动 BGE-M3 嵌入 |
| `JINA_API_KEY` | 联网检索（可选） |
| `BREVO_API_KEY` | 邮件服务（Brevo） |
| `SENDER_EMAIL` | 发件邮箱（Brevo 已验证） |
| `APP_URL` | 公网地址（邮件链接用）`https://app.topsaitech.com.cn` |
| `ALIPAY_APPID` | 支付宝开放平台 APPID |
| `ALIPAY_APP_PRIVATE_KEY` 或 `_PATH` | 应用私钥（PEM 或文件路径） |
| `ALIPAY_PUBLIC_KEY` 或 `_PATH` | 支付宝公钥（PEM 或文件路径） |
| `ALIPAY_NOTIFY_URL` | 回调地址：`…/api/pay/alipay/notify` |
| `ALIPAY_RETURN_URL` | 回跳地址：`…/app?pay=done` |
| `ALIPAY_DEBUG` | `"1"` = 沙箱，不填 = 正式 |

---

## 七、部署方式

```bash
# 本地改完 → 推送
git add <files>
git commit -m "feat/fix: 说明"
git push origin main

# 服务器（SSH）→ 拉取 → 重启
ssh root@111.229.138.131
cd /www/docmind/app
git pull origin main
supervisorctl restart docmind   # 或 systemctl restart docmind
```

**前端静态文件**（index.html / app.html）`git pull` 后无需重启，Ctrl+F5 强刷即可。  
**Python 文件**改动必须重启 uvicorn。

Supervisor 配置参考（`/etc/supervisor/conf.d/docmind.conf`）：
```ini
[program:docmind]
command=bash -c "source /www/docmind/env.sh && uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2"
directory=/www/docmind/app
autostart=true
autorestart=true
stdout_logfile=/www/docmind/app.log
stderr_logfile=/www/docmind/app.log
user=root
```

---

## 八、开发约定（必须遵守）

### 8.1 密钥安全
- **所有 API Key、数据库密码、JWT 密钥只走环境变量**，`os.getenv()` 读取。
- 禁止在代码、注释、提交信息、文档中出现任何真实密钥。
- `DATABASE_URL` 含密码，严禁出现在任何 Git 追踪文件中。

### 8.2 前端三页面同步
修改以下内容时，**必须同步检查三个文件是否需要更新**：
- **套餐名称/价格/权限**：`config.py` PLAN_LIMITS + `app.html` PLAN_LIMITS JS + `index.html` 定价展示
- **联系方式/邮箱**：`app.html` + `index.html` + `services/email_sender.py` 中的 `_SUPPORT_MAIL`
- **功能入口**：导航菜单（`app.html` nav-sidebar）+ 首页功能列表（`index.html`）

### 8.3 权限门控
- 所有需要登录的接口必须 `Depends(get_current_user)`。
- 所有需要邮箱验证的接口调 `require_verified(user)`（在 query.py 里）或检查 `user.email_verified`。
- 套餐权限判断只用 `effective_plan(user)`，不读原始 `user.plan`。
- 会员开通只走 `payment.service.activate()`。

### 8.4 模型路由
- 新增任务类型时，只在 `config.py` 的 `MODEL_ROUTES` 和 `MODEL_FALLBACK` 里加条目。
- 业务代码通过 `MODEL_ROUTES[task_hint]` 取 `(api_key, base_url, model_id)`。
- 视觉分析（GLM）单独走 `vision.py`，不走 MODEL_ROUTES（因为用多模态 API）。

### 8.5 数据库迁移
- 新增表或列先写 SQLAlchemy 模型，再在 `main.py` 的 `_auto_migrate()` 里加 `ALTER TABLE`（幂等）。
- pgvector 的 `doc_chunks` 表由 `embedder.py` 的 `_ensure_table()` 自动建，不走 SQLAlchemy。

### 8.6 合规红线（非常重要）
全站任何文案（前端、官网、法律条款、支付签约资料）**严禁**出现：
`降重` / `降AI率` / `降AIGC` / `代写` / `包过`

统一表述为"学术语言优化""语言润色""表达优化"。
触发后果：支付渠道商户审核、ICP 备案巡检、学术伦理风险。

AI 服务必须使用国内可访问的合规厂商（DeepSeek、硅基流动、智谱）。
**不得改回** Groq / Gemini / OpenAI / Jina——国内服务器上不可用且不合规。

### 8.7 流式响应格式
所有 SSE 流式接口统一格式：
```
data: {"type":"text","text":"..."}  # 文本增量
data: {"type":"sources","sources":[...]}  # 来源（问答结束时）
data: [DONE]  # 结束信号
```
前端用 `authFetch` + ReadableStream 解析，不引入 EventSource（不支持自定义 Header）。

---

## 九、已知问题 / 技术债

| 问题 | 位置 | 说明 |
|------|------|------|
| `requirements.txt` 有 `resend` | requirements.txt | 但代码用 Brevo，应删除 `resend` 依赖 |
| `chroma_db/` 目录残留 | 根目录 | 历史遗留，生产不用；可加入 `.gitignore` |
| `db/database.py` 注释提到 Supabase/Railway | db/database.py | 已迁移到本地 PostgreSQL，注释过时 |
| 旧 payment router 被注释 | main.py L11,57 | `#from routers.payment import router` 可以清理 |
| `enterprise` 套餐前端不再展示 | app.html | 但后端逻辑保留，数据库可能有老用户是此值 |

---

## 十、常用命令速查

```bash
# 查看服务日志
tail -f /www/docmind/app.log

# 重启服务
supervisorctl restart docmind

# 拉取并重启（一键）
cd /www/docmind/app && git pull && supervisorctl restart docmind

# 手动开通套餐（管理员 API）
curl -X POST https://app.topsaitech.com.cn/api/admin/grant \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","plan":"pro","days":30}'

# 批量生成激活码
curl -X POST https://app.topsaitech.com.cn/api/admin/codes \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"plan":"plus","days":30,"count":10,"note":"活动赠送"}'

# 连接本地数据库（服务器上）
source /www/docmind/env.sh
psql $DATABASE_URL
```

---

## 十一、产品定位

DocMind 是面向**国内高校研究生、青年科研人员**的 AI 文献阅读助手。
核心价值：**把文献读薄**——上传 PDF 后可：
- 跨文献知识库问答（每条回答标注原文出处，可溯源）
- 章节精读、图表截图分析（GLM 视觉）
- 生成文献综述、辅助论文撰写
- 导出 BibTeX/APA/MLA 引用格式
- 外文文献联网检索（Semantic Scholar + CrossRef）

**差异化定位**：不是通用 AI 对话工具，而是围绕"一篇文献的完整阅读工作流"设计。
目标是让用户对"英文文献阅读"这件事的心智锚点绑定在 DocMind 上。

---

## 十二、商业模式

### 套餐与履约方式

| plan 值 | 名称 | 定价 | 履约方式 |
|---------|------|------|---------|
| `free` | 免费版 | ¥0 | 自动，注册即得 |
| `plus` | 基础版 | ¥9.9/月 | 支付宝在线支付，自动开通 |
| `pro` | 专业版 | ¥19.9/月 | 支付宝在线支付，自动开通（**主推档**） |
| `custom` | 专属尊享版 | 定制报价 | 人工成交 → 管理后台手动开通 |

**前三档**是软件产品（边际成本近零，可规模化）。  
**custom** 是"AI 工具 + 真人学术顾问"混合服务（人工精修、投稿护航、返修支持），
不做线上支付，走：咨询表单 → `leads` 表 → 邮件通知管理员 → 人工谈单 → `admin/grant` 手动开通。

### 支付渠道
- **主渠道**：支付宝电脑网站支付（APPID `2021006171620934`，费率 0.6%）
- **激活码**：淘宝卡密 / 活动赠送，前缀 DMPLS/DMPRO/DMCUS
- **管理员手动**：`POST /api/admin/grant`（需 admin JWT）

不自动续费，按月单次付费，到期自动降回免费版。

---

## 十三、近期规划（按优先级）

1. **【进行中】** 管理后台 admin 模块：套餐/联系方式/首页文案/公告可视化编辑

2. **【高优先】** 定价信息动态化  
   当前套餐信息硬编码在 **5 处**，任何调价需要同步改：
   - `config.py` → `PLAN_LIMITS`
   - `payment/service.py` → `PLANS`
   - `frontend/index.html` → 定价卡片
   - `frontend/app.html` → 定价弹窗
   - 官网 `/www/wwwroot/topsaitech.com.cn/index.html`（独立静态站，不在本仓库）  
   
   改造目标：单一数据源 `GET /api/site/config`，前端读接口渲染，改一处全站同步。

3. **【中优先】** 拆分 `app.html`（~3400 行）为模块化 JS 文件，不引入构建工具

4. **【低优先】** Google Fonts 本地化（国内访问不稳定）

5. **【低优先】** `finish_reason` 检测：LLM 因 `max_tokens` 截断时给出友好提示 + 升级引导

6. **【待规模化】** 微信支付、团队版多席位、算法备案

---

## 十四、页面与设计规范

### 三套前端

| 页面 | 路径 / 域名 | 仓库位置 |
|------|------------|---------|
| 官网 | `www.topsaitech.com.cn` | `/www/wwwroot/`（**不在本仓库**） |
| 落地页 | `app.topsaitech.com.cn/` | `frontend/index.html` |
| 工作台 | `app.topsaitech.com.cn/app` | `frontend/app.html` |

### 视觉风格

- **官网**：浅色纸白 + 墨蓝 + 高亮笔黄，学术批注感
- **落地页 + 工作台**：暗色 `#0f0f1a` + 金色 `#c9a84c`，衬线标题

CSS 变量（工作台）：`--bg` `--card` `--border` `--gold` `--fg` `--muted`

### 约束
- 不引入 UI 框架，纯手写 CSS
- **所有外部资源必须本地化**（国内访问 CDN / Google 字体不稳定）
  - PDF.js 已本地化：`frontend/pdf.min.js` + `frontend/pdf.worker.min.js`
  - 新增第三方库同样须下载到 `frontend/` 后引用
- 移动端必须可用：现有断点 `820px` / `900px`
- SSE 流式输出必须逐字显示——Nginx 须配置 `proxy_buffering off`

---

## 十五、开发纪律补充

- **提交前安全检查**：`git diff | grep -E "sk-|key=|password="`，确认无密钥泄漏
- **语法检查**：Python 改动后 `python3 -c "import main"` 无报错再提交
- **数据库备份确认**：改 schema 前确认 `/www/backup/pg/` 有当日备份（凌晨 3:30 自动备份）
- **前端改动三页联查**：定价、联系方式、合规文案改动须同步检查 index.html / app.html / 官网
- **合规自检**：新增任何文案后搜索 `降重|降AI|代写|包过`，一旦出现立即删除
