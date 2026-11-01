#!/usr/bin/env python3
"""
AI 代码评审：把 diff 交给大模型评审，并把结果发布到对应位置。

环境变量：
  GH_TOKEN          GitHub 令牌（取 diff / 发评论）
  DEEPSEEK_API_KEY  DeepSeek 密钥（优先）
  DASHSCOPE_API_KEY 通义千问密钥（备用）
  REVIEW_MODE   pr | commit
                pr     -> 评审 PR 整体 diff，评论发到 PR 上
                commit -> 评审某次 push 的增量 diff，评论发到该提交上
  PR_NUMBER     PR 编号（pr 模式）
  COMMIT_SHA    提交 SHA（commit 模式）
  REVIEW_BASE   基线提交（commit 模式，通常为 push 前的 SHA）
  REPO          owner/repo（commit 模式发评论用）
"""
import os
import json
import subprocess
import urllib.request

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
PR = os.environ.get("PR_NUMBER", "").strip()
SHA = os.environ.get("COMMIT_SHA", "").strip()
BASE = os.environ.get("REVIEW_BASE", "").strip()
REPO = os.environ.get("REPO", "").strip()
MODE = os.environ.get("REVIEW_MODE", "pr").strip().lower()
TOKEN = os.environ.get("GH_TOKEN", "")

MAX_DIFF = 16000  # 截断，避免超上下文长度


def fail(msg):
    print(msg)
    raise SystemExit(0)


def pick_model():
    if DEEPSEEK_KEY:
        return ("https://api.deepseek.com/chat/completions", "deepseek-chat", DEEPSEEK_KEY)
    if DASHSCOPE_KEY:
        return (
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "qwen-plus",
            DASHSCOPE_KEY,
        )
    fail("未配置 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY，跳过评审")


def get_diff():
    """按模式获取待评审的 diff 文本。"""
    if MODE == "pr":
        if not PR:
            fail("pr 模式缺少 PR_NUMBER")
        return subprocess.run(["gh", "pr", "diff", PR], capture_output=True, text=True).stdout
    if not SHA:
        fail("commit 模式缺少 COMMIT_SHA")
    base = BASE
    if not base or set(base) == {"0"}:
        base = SHA + "^"  # 首次推送没有基线，退化为与父提交比较
    for sep in ("...", ".."):
        res = subprocess.run(
            ["git", "diff", "--no-color", base + sep + SHA], capture_output=True, text=True
        )
        if res.returncode == 0:
            return res.stdout
    fail("获取 diff 失败")

def build_prompt(diff):
    return f"""你是一名资深代码评审专家。请评审以下代码变更（ERP 系统：后端 FastAPI/Python，前端 Vue/TypeScript/Vite）。

要求：
- 用中文输出；
- 分「🚫 严重问题 / 💡 建议改进 / ✅ 亮点」三类；
- 每条给出文件与大致位置、问题描述、修改建议；
- 不要整段粘贴代码，只给要点；
- 若问题由本次变更引入，请在「严重问题」中明确指出「本次变更引入」；
- 若无明显问题，明确写「未发现问题」。

===== diff =====
{diff}
"""


def call_model(diff):
    url, model, key = pick_model()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": build_prompt(diff)}],
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        return f"⚠️ AI 评审调用失败：{e}"


def publish(review):
    comment = "## 🤖 AI 代码评审\n\n" + review
    if MODE == "pr":
        subprocess.run(["gh", "pr", "comment", PR, "--body", comment], check=True)
        print("AI 评审已发布到 PR #%s" % PR)
        return
    if not REPO:
        fail("commit 模式缺少 REPO")
    path = "/tmp/ai-review.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(comment)
    # 发布为提交评论：显示在该 commit 页面与 PR 的 Changes 列表中
    subprocess.run(
        [
            "gh", "api", "-X", "POST",
            "repos/%s/commits/%s/comments" % (REPO, SHA),
            "-F", "body=@" + path,
        ],
        check=True,
    )
    print("AI 评审已发布到提交 %s" % SHA)


def main():
    if not TOKEN:
        fail("缺少 GH_TOKEN")
    diff = get_diff()
    if not diff.strip():
        fail("无变更内容，跳过评审")
    review = call_model(diff[:MAX_DIFF])
    publish(review)


if __name__ == "__main__":
    main()