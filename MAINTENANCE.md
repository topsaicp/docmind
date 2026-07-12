# DocMind 维护指南

## 一、修改功能（日常开发流程）

在本地（WSL）改代码，推送到服务器：

```bash
# 1. 本地改完代码后提交
git add .
git commit -m "说明改了什么"
git push origin main

# 2. 服务器上拉取最新代码
ssh root@111.229.138.131
cd /www/docmind/app
git pull origin main

# 3. 重启服务
kill $(pgrep -f uvicorn)
source /www/docmind/env.sh && \
  nohup uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2 \
  > /www/docmind/app.log 2>&1 &
```

---

## 二、设置 Supervisor 自动守护

避免进程崩溃后手动重启，在宝塔「软件商店」安装 **Supervisor**，新建配置：

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

之后重启只需：

```bash
supervisorctl restart docmind
```

---

## 三、查看日志（排查问题必备）

```bash
# 实时看日志
tail -f /www/docmind/app.log

# 看最后 100 行
tail -100 /www/docmind/app.log

# 搜索错误
grep "ERROR\|Exception\|500" /www/docmind/app.log
```

---

## 四、数据库维护

```bash
# 连接 Supabase 查看用户
psql $DATABASE_URL -c "SELECT email, plan, pdf_count, created_at FROM users ORDER BY created_at DESC LIMIT 20;"

# 手动激活用户套餐
curl -X POST https://app.topsaitech.com.cn/api/auth/admin/activate \
  -H "Content-Type: application/json" \
  -d '{"email":"用户邮箱","plan":"pro","days":30,"admin_secret":"你的ADMIN_SECRET"}'
```

---

## 五、更新依赖

```bash
cd /www/docmind/app
pip install -r requirements.txt --upgrade
supervisorctl restart docmind
```

---

## 六、备份建议

| 内容 | 备份方式 |
|------|------|
| 代码 | GitHub 已自动备份 |
| 数据库 | Supabase 控制台开启自动备份 |
| 上传的 PDF | `/www/docmind/app/uploads/` 定期压缩到腾讯云 COS |
| 向量库 | `/www/docmind/app/chroma_db/` 定期压缩到腾讯云 COS |

---

## 七、常见操作速查

```bash
# 查看服务状态
ps aux | grep uvicorn

# 重启服务
supervisorctl restart docmind

# 拉取最新代码并重启（一键）
cd /www/docmind/app && git pull && supervisorctl restart docmind

# 查看端口占用
ss -tlnp | grep 8000
```

---

## 八、关键路径

| 项目 | 路径 / 地址 |
|------|------|
| 代码目录 | `/www/docmind/app/` |
| 环境变量 | `/www/docmind/env.sh` |
| 运行日志 | `/www/docmind/app.log` |
| PDF 上传目录 | `/www/docmind/app/uploads/` |
| 向量库 | `/www/docmind/app/chroma_db/` |
| GitHub 仓库 | https://github.com/topsaicp/docmind |
| 线上地址 | https://app.topsaitech.com.cn |
| 宝塔面板 | http://111.229.138.131:8888 |

---

> **核心流程**：本地改代码 → `git push` → 服务器 `git pull` → `supervisorctl restart docmind`
