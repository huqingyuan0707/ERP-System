#!/usr/bin/env python3
"""AI 代码评审（门禁版）。

相对旧版的三个升级点：

1. **带上下文评审**：把仓库里的需求基线（`需求条目拆分.md`）和代码规范
   （`.trae/rules/code-style.md`）一起喂给模型，评审结论能对照「这个功能
   到底该怎么做」和「本项目硬性规范是什么」，而不是只看 diff 猜；
2. **结构化输出**：要求模型返回 JSON（风险等级 / 问题清单 / 亮点 / 建议），
   便于程序判定，而不是只能靠人读文本；
3. **可阻断门禁**：PR 模式下若判定为高风险，脚本以非 0 退出码结束，
   配合 GitHub 分支保护即可阻断合并；commit 模式只评论不阻断。

环境变量：
  GH_TOKEN           GitHub 令牌（取 diff / 发评论，必需）
  DEEPSEEK_API_KEY   DeepSeek 密钥（优先）
  DASHSCOPE_API_KEY  通义千问密钥（备用）
  REVIEW_MODE   pr | commit
                 pr     -> 评审 PR 整体 diff，评论发到 PR 上
                 commit -> 评审某次 push 的增量 diff，评论发到该提交上
  PR_NUMBER        PR 编号（pr 模式）
  COMMIT_SHA       提交 SHA（commit 模式）
  REVIEW_BASE      基线提交（commit 模式，通常为 push 前的 SHA）
  REPO             owner/repo（commit 模式发评论用）
  REVIEW_ENFORCE   1/true 时允许因高风险阻断合并（PR 模式默认开启）
  REVIEW_FAIL_OPEN 1/true 时模型不可用按「放行」处理（默认放行，避免
                    模型服务抖动把整个团队的 PR 全卡住）
  REVIEW_DRY_RUN   1/true 时只打印不调模型、不发评论（本地调试用）
"""

import json
import os
import re
import subprocess
import urllib.request


# DeepSeek 密钥：优先使用
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
# 通义千问密钥：DeepSeek 不可用时的备用
DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
# 当前评审的 PR 编号，pr 模式必填
PR = os.environ.get("PR_NUMBER", "").strip()
# 当前评审的提交 SHA，commit 模式必填
SHA = os.environ.get("COMMIT_SHA", "").strip()
# 基线提交，commit 模式下用于算增量 diff
BASE = os.environ.get("REVIEW_BASE", "").strip()
# 仓库全名，形如 owner/repo，commit 模式发评论要用
REPO = os.environ.get("REPO", "").strip()
# 评审模式：pr 或 commit
MODE = os.environ.get("REVIEW_MODE", "pr").strip().lower()
# GitHub 令牌
TOKEN = os.environ.get("GH_TOKEN", "")
# 是否允许因高风险阻断合并
ENFORCE = os.environ.get("REVIEW_ENFORCE", "1").strip().lower() in ("1", "true", "yes")
# 模型不可用时是否放行
FAIL_OPEN = os.environ.get("REVIEW_FAIL_OPEN", "1").strip().lower() in ("1", "true", "yes")
# 本地调试模式：不调模型、不发评论
DRY_RUN = os.environ.get("REVIEW_DRY_RUN", "").strip().lower() in ("1", "true", "yes")

# diff 最多截取多少字符，避免超出模型上下文
MAX_DIFF = 12000
# 需求基线最多注入多少字符
MAX_REQUIREMENT = 4000
# 代码规范最多注入多少字符
MAX_RULES = 6000
# 模型请求超时（秒）
MODEL_TIMEOUT = 120

# 需求基线文件路径：开发排期的唯一事实来源
REQUIREMENT_FILE = "需求条目拆分.md"
# 代码规范文件路径：本项目强制的书写规范
RULES_FILE = ".trae/rules/code-style.md"

# 文件名关键词 -> 中文业务关键词，用于从需求基线里捞出相关条目
MODULE_KEYWORDS = {
    "inventory": "库存",
    "stock": "库存",
    "warehouse": "仓库",
    "goods": "商品",
    "supplier": "供应商",
    "customer": "客户",
    "purchase": "采购",
    "sale": "销售",
    "finance": "财务",
    "receivable": "应收",
    "payable": "应付",
    "auth": "登录",
    "user": "用户",
    "role": "角色",
    "dept": "部门",
    "permission": "权限",
    "dict": "字典",
    "config": "参数",
    "task": "任务",
    "oper_log": "操作日志",
    "login_log": "登录日志",
    "excel": "导出",
    "importer": "导入",
    "migration": "迁移",
    "alembic": "迁移",
}

# 高危路径规则：(正则, 原因说明)。命中任意一条即至少判定为高风险
HIGH_RISK_RULES = [
    (r"backend/app/(services|repositories|models)/inventory\.py", "库存数量变动，直接影响账实相符"),
    (r"backend/app/services/(finance|receivable|payable)", "应收应付与账务，错了就是财务事故"),
    (r"backend/app/services/(purchase|sale|transfer|check)", "采购/销售/调拨/盘点单据确认流程"),
    (r"backend/alembic/versions/", "数据库结构变更，线上不可逆"),
    (r"backend/app/core/(security|deps)\.py", "鉴权与权限，越权风险最高"),
    (r"backend/app/core/config\.py", "全局配置，影响所有请求"),
    (r"backend/app/db/init_db\.py", "初始化种子数据，影响环境一致性"),
]

# 中危路径规则：命中则至少判定为中风险
MEDIUM_RISK_RULES = [
    (r"backend/app/api/v1/endpoints/", "对外接口层，改动会影响前端契约"),
    (r"backend/app/repositories/", "数据访问层，SQL 改动影响面大"),
    (r"backend/app/utils/bill_no\.py", "单号生成，重复会破坏单据唯一性"),
    (r"frontend/src/(api|stores|utils)/", "前端请求与全局状态，影响面广"),
    (r"\.github/workflows/", "流水线配置，误改会导致发布失控"),
]


def skip(msg):
    """无需评审时跳过：退出码 0，不视为失败。"""
    print(f"[跳过] {msg}")
    raise SystemExit(0)


def error(msg):
    """真正的故障：退出码非 0，避免问题被当成正常跳过而静默。"""
    print(f"[错误] {msg}")
    raise SystemExit(1)


def pick_model():
    """选择可用的模型服务，返回 (接口地址, 模型名, 密钥)。"""
    if DEEPSEEK_KEY:
        return ("https://api.deepseek.com/chat/completions", "deepseek-chat", DEEPSEEK_KEY)
    if DASHSCOPE_KEY:
        return (
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "qwen-plus",
            DASHSCOPE_KEY,
        )
    skip("未配置 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY，无法评审")


def get_diff():
    """按模式获取待评审的 diff 文本。"""
    if MODE == "pr":
        # PR 模式：用 gh 拿整个 PR 的 diff
        if not PR:
            error("pr 模式缺少 PR_NUMBER")
        return subprocess.run(["gh", "pr", "diff", PR], capture_output=True, text=True).stdout
    if not SHA:
        error("commit 模式缺少 COMMIT_SHA")
    base = BASE
    if not base or set(base) == {"0"}:
        # 首次推送没有基线，退化为与父提交比较
        base = SHA + "^"
    # push 场景用两点比较（base..sha）= 本次提交的增量；
    # 三点是 merge-base 语义，范围可能与预期不一致，这里不混用
    res = subprocess.run(
        ["git", "diff", "--no-color", base + ".." + SHA], capture_output=True, text=True
    )
    if res.returncode != 0:
        error(f"获取 diff 失败：{res.stderr.strip()[:200]}")
    return res.stdout


def changed_files(diff):
    """从 diff 文本里解析出被修改的文件路径列表。"""
    files = []
    # diff 中每个新文件以 "+++ b/路径" 开头
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            if path and path != "/dev/null" and path not in files:
                files.append(path)
    return files


def keywords_for(files):
    """根据变更文件路径推导中文业务关键词，用于检索需求基线。"""
    words = set()
    for path in files:
        lowered = path.lower()
        for key, word in MODULE_KEYWORDS.items():
            if key in lowered:
                words.add(word)
    return words


def read_text(path):
    """读取仓库中的文本文件，失败返回空字符串（缺文件不应让评审整体崩掉）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def build_requirement_context(files):
    """从需求基线文档里抽取与本次变更相关的条目。

    需求基线有 8 万多字，全量塞给模型既超上下文又会稀释重点，
    所以这里只保留「目录标题 + 命中关键词的条目行」。
    """
    content = read_text(REQUIREMENT_FILE)
    if not content:
        return ""
    words = keywords_for(files)
    lines = content.splitlines()
    heads = []  # 文档结构（标题行），让模型知道各模块条目编号
    matched = []  # 命中业务关键词的需求条目
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heads.append(stripped)
            continue
        if words and any(word in stripped for word in words):
            matched.append(stripped)
    parts = []
    if heads:
        # 标题行最多留 60 条，防止目录本身过长
        parts.append("【需求条目目录】\n" + "\n".join(heads[:60]))
    if matched:
        parts.append("【与本次变更相关的需求条目】\n" + "\n".join(matched[:80]))
    text = "\n\n".join(parts)
    return text[:MAX_REQUIREMENT]


def build_rules_context():
    """读取项目代码规范原文，作为评审依据。"""
    content = read_text(RULES_FILE)
    if not content:
        return ""
    return content[:MAX_RULES]


def assess_path_risk(files):
    """按路径规则判定风险等级，返回 (等级, 原因列表)。

    这是确定性规则，不依赖模型判断：只要改了库存、账务、单据确认、
    数据库结构、鉴权这类文件，就一定是高风险，必须人工确认。
    """
    reasons = []
    level = "LOW"
    for path in files:
        for pattern, reason in HIGH_RISK_RULES:
            if re.search(pattern, path):
                reasons.append(f"高危：`{path}` 属于 {reason}")
                level = "HIGH"
        for pattern, reason in MEDIUM_RISK_RULES:
            if re.search(pattern, path):
                reasons.append(f"中危：`{path}` 属于 {reason}")
                if level == "LOW":
                    level = "MEDIUM"
    # 去重但保留顺序
    seen = set()
    unique = []
    for item in reasons:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return level, unique


def build_prompt(diff, files, requirement_ctx, rules_ctx, path_risk, path_reasons):
    """组装发给模型的提示词。"""
    risk_hint = path_risk
    if path_reasons:
        risk_hint += "（本地规则已识别的风险点：" + "；".join(path_reasons) + "）"
    # 用 .format 而不是 f-string，避免正文里的花括号干扰格式化
    return """你是一名资深 ERP 代码评审专家。
项目背景：后端 FastAPI + SQLAlchemy 2.0（异步）+ PostgreSQL + Pydantic v2；
前端 Vue 3 + TypeScript + Vite + Element Plus。
系统特点：业务链路长、涉及库存与账务数据、变更风险高，因此对金额、库存数量、
单据状态流转、权限校验、数据一致性方面的缺陷要特别严格。

请评审下面的代码变更，并**只输出一个 JSON 对象**（不要包裹 markdown 代码块）：

{{
  "riskLevel": "HIGH | MEDIUM | LOW",
  "summary": "一句话概述这次变更做了什么、风险在哪",
  "issues": [
    {{
      "severity": "critical | major | minor",
      "file": "文件路径",
      "line": 123,
      "problem": "问题描述（说明会造成什么后果）",
      "fix": "具体修改建议"
    }}
  ],
  "highlights": ["值得肯定的地方"],
  "suggestions": ["非阻塞的改进建议"]
}}

判定口径：
- riskLevel=HIGH：存在会破坏业务正确性、数据一致性或权限安全的问题；
- riskLevel=MEDIUM：有明确缺陷但不致命，或缺少测试/校验；
- riskLevel=LOW：整体没问题，仅有风格建议。
本地路径规则给出的初始风险等级是：{risk_hint}，你可以在此基础上上下调整。

评审要求：
1. 只报本次变更引入或本次变更暴露的问题，不要翻旧账；
2. 每条问题必须指出文件与位置，并写清「不改会怎样」；
3. 严格对照下面给出的《项目代码规范》与《需求条目》，规范里明确禁止的写法一律算 major 以上；
4. 需求条目里写明的业务规则被违反，算 critical；
5. 没有问题时 issues 给空数组，summary 写「未发现问题」。

===== 项目代码规范 =====
{rules_ctx}

===== 相关需求条目 =====
{requirement_ctx}

===== 本次变更涉及的文件 =====
{files}

===== diff =====
{diff}
""".format(
        risk_hint=risk_hint,
        rules_ctx=rules_ctx or "（未找到规范文件）",
        requirement_ctx=requirement_ctx or "（未找到需求基线文件）",
        files="\n".join("- " + f for f in files) or "（无）",
        diff=diff,
    )


def call_model(prompt):
    """调用大模型，返回 (原始文本, 是否成功)。失败时返回失败而不是抛异常。"""
    url, model, key = pick_model()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=MODEL_TIMEOUT) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"], True
    except Exception as exc:  # noqa: BLE001  任何网络/格式异常都要降级而不是炸流水线
        return f"⚠️ AI 评审调用失败：{exc}", False


def parse_review(raw):
    """把模型输出解析成结构化结果；解析不了就退化成纯文本评审。"""
    text = raw.strip()
    # 去掉模型可能加上的 ```json 包裹
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return normalize_review(data)
        except (ValueError, TypeError):
            pass
    # 模型没按 JSON 输出：整体当作纯文本评论，级别按中风险处理
    return {
        "riskLevel": "MEDIUM",
        "summary": "模型未返回结构化结果，以下为原始评审内容",
        "issues": [],
        "highlights": [],
        "suggestions": [],
        "raw": text,
    }


def normalize_review(data):
    """把模型返回的字段规整成固定结构，缺字段用默认值补齐。"""
    level = str(data.get("riskLevel", "LOW")).upper()
    if level not in ("HIGH", "MEDIUM", "LOW"):
        level = "LOW"
    issues = []
    for item in data.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append(
            {
                "severity": str(item.get("severity", "minor")).lower(),
                "file": str(item.get("file", "")),
                "line": item.get("line", ""),
                "problem": str(item.get("problem", "")),
                "fix": str(item.get("fix", "")),
            }
        )
    return {
        "riskLevel": level,
        "summary": str(data.get("summary", "")),
        "issues": issues,
        "highlights": [str(x) for x in (data.get("highlights") or [])],
        "suggestions": [str(x) for x in (data.get("suggestions") or [])],
        "raw": "",
    }


def higher_level(a, b):
    """取两个风险等级中更高的一个。"""
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def build_comment(review, path_reasons, blocked):
    """把结构化评审结果渲染成 Markdown 评论。"""
    level = review["riskLevel"]
    badge = {"HIGH": "🔴 高风险", "MEDIUM": "🟡 中风险", "LOW": "🟢 低风险"}.get(level, level)
    lines = ["## 🤖 AI 代码评审", "", f"**风险等级：{badge}**", ""]
    summary = review.get("summary") or "（模型未给出概述）"
    lines += [summary, ""]
    if path_reasons:
        lines.append("**本地路径规则命中：**")
        lines += ["- " + reason for reason in path_reasons]
        lines.append("")
    issues = review.get("issues") or []
    if issues:
        lines.append("### 🚫 问题清单")
        for item in issues:
            severity = item.get("severity", "minor")
            icon = {"critical": "🔴", "major": "🟠", "minor": "🟡"}.get(severity, "🟡")
            location = item.get("file", "")
            line_no = item.get("line", "")
            if location and line_no:
                location = f"{location}:{line_no}"
            lines.append("")
            lines.append(f"- {icon} **{location or '未指明位置'}**")
            lines.append(f"  - 问题：{item.get('problem', '')}")
            lines.append(f"  - 建议：{item.get('fix', '')}")
        lines.append("")
    else:
        lines.append("### 🚫 问题清单")
        lines.append("未发现阻塞性问题。")
        lines.append("")
    highlights = review.get("highlights") or []
    if highlights:
        lines.append("### ✅ 亮点")
        lines += ["- " + item for item in highlights]
        lines.append("")
    suggestions = review.get("suggestions") or []
    if suggestions:
        lines.append("### 💡 建议改进")
        lines += ["- " + item for item in suggestions]
        lines.append("")
    if blocked:
        lines.append("---")
        lines.append(
            "> ⛔ **本次评审判定为高风险，流水线已阻断合并**，请按上面的问题修复后重新推送。"
        )
    return "\n".join(lines)


def publish(comment):
    """把评审评论发布到 PR 或提交上。"""
    if MODE == "pr":
        subprocess.run(["gh", "pr", "comment", PR, "--body", comment], check=True)
        print(f"AI 评审已发布到 PR #{PR}")
        return
    if not REPO:
        error("commit 模式缺少 REPO")
    path = "/tmp/ai-review.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(comment)
    # 发布为提交评论：显示在该 commit 页面与 PR 的 Changes 列表中
    subprocess.run(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"repos/{REPO}/commits/{SHA}/comments",
            "-F",
            "body=@" + path,
        ],
        check=True,
    )
    print(f"AI 评审已发布到提交 {SHA}")


def main():
    """编排整个评审流程。"""
    if not TOKEN:
        error("缺少 GH_TOKEN")
    diff = get_diff()
    if not diff.strip():
        skip("无变更内容，无需评审")
    files = changed_files(diff)
    print(f"本次变更涉及 {len(files)} 个文件：{', '.join(files) or '无'}")
    # 先用确定性路径规则给出一个保底风险等级
    path_level, path_reasons = assess_path_risk(files)
    if path_reasons:
        print(f"路径规则判定风险等级：{path_level}")
    requirement_ctx = build_requirement_context(files)
    rules_ctx = build_rules_context()
    prompt = build_prompt(
        diff[:MAX_DIFF], files, requirement_ctx, rules_ctx, path_level, path_reasons
    )
    if DRY_RUN:
        print("=== DRY RUN：以下为将要发送的提示词 ===")
        print(prompt)
        return
    raw, ok = call_model(prompt)
    if not ok:
        # 模型不可用：按配置决定放行还是阻断，默认放行，避免模型抖动卡住团队
        comment = "## 🤖 AI 代码评审\n\n" + raw + "\n\n> 本次评审未能完成，未产生门禁结论。"
        try:
            publish(comment)
        except Exception as exc:  # noqa: BLE001
            print(f"发布失败评论也失败了：{exc}")
        if FAIL_OPEN:
            print("::warning::AI 评审服务不可用，按放行处理")
            raise SystemExit(0)
        error("AI 评审服务不可用，按阻断处理")
    review = parse_review(raw)
    # 模型判定的等级与本地路径规则取更高者，宁可多拦一次也不放过高危改动
    final_level = higher_level(review["riskLevel"], path_level)
    review["riskLevel"] = final_level
    # 模型说 HIGH，或本地规则命中高危路径，均视为需阻断
    blocked = ENFORCE and final_level == "HIGH"
    comment = build_comment(review, path_reasons, blocked)
    try:
        publish(comment)
    except Exception as exc:  # noqa: BLE001
        # 评论发布失败（例如 fork PR 无写权限）不应让流程中断，
        # 但门禁结论依旧生效，所以这里只告警不退出
        print(f"::warning::AI 评审评论发布失败：{exc}")
    print(f"最终风险等级：{final_level}，阻断合并：{'是' if blocked else '否'}")
    if blocked:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
