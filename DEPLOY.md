# 部署与公开访问（不暴露 API 密钥）

仓库地址：<https://github.com/Lesring/Danji>

## 原则

| 放在哪里 | 内容 |
| --- | --- |
| **GitHub 仓库** | 源码、`backend/.env.example`（占位符）、文档 |
| **绝不提交** | `backend/.env`、任何真实 `*_API_KEY` |
| **公网演示** | 环境变量 `EXTRACT_MODE=fixture`（内置规则抽取，不调用模型） |
| **你自己要用 LLM/OCR/ASR** | 只在本地 `.env` 或云平台「Environment Variables」里填密钥 |

若 `.env` 曾经被误提交过，请到各服务商**轮换密钥**，并在 GitHub → Settings → Secrets and variables 中检查是否有多余暴露。

---

## 方式一：别人从 GitHub 拉代码，本地运行（推荐评审 / 队友）

无需 API，与 README 一致：

```powershell
cd backend
python -m pip install -r requirements.txt
# 不要创建 .env，或复制 .env.example 并保持 EXTRACT_MODE=fixture
python -m uvicorn app.main:app --reload --port 8000
```

另开终端：

```powershell
cd frontend
npm install
npm run dev
```

浏览器打开 <http://127.0.0.1:5173>。演示解析走内置 fixture，**不会访问外网模型**。

---

## 方式二：公网 URL（Render 等），仍不暴露密钥

1. 把本仓库推到 GitHub（见下方「首次推送」）。
2. 注册 [Render](https://render.com)，用 GitHub 登录。
3. **New → Blueprint**，选择 `Lesring/Danji`，确认 `render.yaml`。
4. 部署完成后得到 `https://xxx.onrender.com`，**前后端一体**（Docker 内已构建前端并设 `STATIC_DIR`）。
5. 在 Render 服务 → **Environment** 中确认：
   - `EXTRACT_MODE` = `fixture`（Blueprint 已写，勿在仓库里写密钥）
6. 若**仅你自己**要在公网实例上开 LLM：在 Render 控制台添加 `LLM_API_KEY` 等，**不要**写进 Git；并把 `EXTRACT_MODE` 改为 `llm`。

免费实例会休眠，首访可能较慢，属平台限制。

---

## 方式三：Docker 本地或自有服务器

```powershell
docker build -t danji .
docker run --rm -p 8000:8000 -e EXTRACT_MODE=fixture danji
```

浏览器打开 <http://127.0.0.1:8000>。

需要模型时，用 `-e` 传入密钥（仅运行时，不进镜像）：

```powershell
docker run --rm -p 8000:8000 --env-file backend/.env danji
```

`.env` 文件只留在本机，不要 `docker build` 进镜像（本仓库 Dockerfile 未 COPY `.env`）。

---

## 首次推送到 GitHub

在项目根目录（PowerShell）：

```powershell
git init -b main
git add -A
git status   # 确认列表里没有 backend/.env
git commit -m "Initial commit: 单迹订单协同智能体"
git remote add origin https://github.com/Lesring/Danji.git
git push -u origin main
```

推送前务必执行 `git status`，若出现 `backend/.env`，先 `git rm --cached backend/.env` 并确认 `.gitignore` 已生效。

---

## GitHub 上还能做什么

- **公开仓库**：任何人可 clone / fork，按方式一运行；**看不到你的密钥**。
- **GitHub Actions**：本仓库 `ci.yml` 仅跑 pytest，使用 `EXTRACT_MODE=fixture`，不需要 Secrets。
- **若以后用 Actions 自动部署**：在 Repo → Settings → Secrets and variables → Actions 里添加 `LLM_API_KEY` 等，在 workflow 里 `env:` 引用；**仍不要**写进代码或日志。

---

## 不适合的做法

- 把 `.env` 或密钥写进 README、Issue、PR 评论。
- 在前端代码里写死 API Key（浏览器会泄露）。
- 仅靠 GitHub Pages 托管前端：API 仍需单独后端；若后端无密钥，请用 fixture 或反向代理到已配置密钥的服务器（密钥只在服务器环境变量中）。
