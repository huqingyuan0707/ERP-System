#!/usr/bin/env python3
"""
AI 代码评审：在 PR 上自动用大模型评审 diff 并评论。

环境变量：
  GH_TOKEN          GitHub 默认令牌（用于取 diff、发评论）
  PR_NUMBER         PR 编号
  DEEPSEEK_API_KEY  DeepSeek 密钥（优先使用）
  DASHSCOPE_API_KEY 通义千问密钥（DeepSeek 未配置时备用）

模型接口均为 OpenAI 兼容格式，可自由替换。
"""
import os
import json
import subprocess
import urllib.request

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
PR = os.environ.get("PR_NUMBER", "")
TOKEN = os.environ.get("GH_TOKEN", "")

if not (PR and TOKEN):
    print("缺少 PR_NUMBER / GH_TOKEN，跳过评审")
    raise SystemExit(0)

if DEEPSEEK_KEY:
    API_URL = "https://api.deepseek.com/chat/completions"
    API_MODEL = "deepseek-chat"
    API_KEY = DEEPSEEK_KEY
elif DASHSCOPE_KEY:
    API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    API_MODEL = "qwen-plus"
    API_KEY = DASHSCOPE_KEY
else:
    print("未配置 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY，跳过评审")
    raise SystemExit(0)

# 1) 取 PR diff
diff = subprocess.run(["gh", "pr", "diff", PR], capture_output=True, text=True).stdout
if not diff.strip():
    print("无 diff，跳过")
    raise SystemExit(0)
diff = diff[:16000]  # 截断，避免超上下文长度

prompt = f"""你是一名资深代码评审专家。请评审以下 Pull Request 的 diff（ERP 系统：后端 FastAPI/Python，前端 Vue/TypeScript/Vite）。

要求：
- 用中文输出；
- 分「🚫 严重问题 / 💡 建议改进 / ✅ 亮点」三类；
- 每条给出文件与大致位置、问题描述、修改建议；
- 不要整段粘贴代码，只给要点；
- 若无明显问题，明确写「未发现问题」。

===== diff =====
{diff}
"""

payload = {
    "model": API_MODEL,
    "messages": [{"role": "user", "content": prompt}],
    "temperature": 0.3,
}
req = urllib.request.Request(
    API_URL,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
)
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    review = data["choices"][0]["message"]["content"]
except Exception as e:  # noqa: BLE001
    review = f"⚠️ AI 评审调用失败：{e}"

comment = "## 🤖 AI 代码评审\n\n" + review
subprocess.run(["gh", "pr", "comment", PR, "--body", comment], check=True)
print("AI 评审已发布到 PR #%s" % PR)
