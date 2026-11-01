"""代码质量门禁判定脚本。

职责：把 pylint / pytype 的检查结果，按「模块风险等级」和「问题类别」分级判定，
输出统一的 JSON + Markdown 报告，并返回对应的退出码供 CI 阻断合并。

分级策略（核心思想：财务/库存数据正确性优先，格式问题不阻断）
------------------------------------------------------------------
高风险模块（财务、库存、成本、单据、权限）：
  - 阻断项：类型错误、空引用、非法调用、参数不匹配、未定义变量等
  - 告警项：格式、命名、复杂度、文档缺失等

普通模块：
  - 阻断项：仅「未定义变量」「参数不匹配」这类几乎必然是 bug 的问题
  - 告警项：其余全部

存量基线（baseline）：
  - 基线内的存量问题不阻断，只在报告中提示（避免存量债务卡死所有 MR）
  - 但「关键缺陷类别」（空引用、类型不匹配等）无论是否新增都阻断：
    关键缺陷表示 ERP 财务/库存数据可能算错，必须当场修掉
  - 基线指纹保留完整消息文本与出现次数，避免「不同变量名的同类问题」
    被错误合并成一条基线，从而让新缺陷蒙混过关

失败语义（fail-closed，检查没跑成功绝不放行）：
  - 退出码 0：检查完整执行，且无阻断问题
  - 退出码 1：检查完整执行，但存在阻断问题
  - 退出码 2：检查本身失败（缺报告、工具崩溃、配置错误、报告不完整）

用法：
  python scripts/ci/quality_gate.py --scan-status reports/scan-status.json \
      --pylint-json reports/pylint.json --pytype-output reports/pytype.txt \
      --baseline backend/quality_baseline.json --output reports/quality
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# 高风险模块判定
# ---------------------------------------------------------------------------
# ERP 系统中一旦出错会导致财务/库存数据错误的模块。
# 命中任一关键字即视为高风险。
HIGH_RISK_PATTERNS = (
    # 财务：凭证、应收应付、费用、结算、对账、利润
    "finance",
    "fin_",
    "billing",
    "payment",
    "settlement",
    "invoice",
    "voucher",
    "ledger",
    "receivable",
    "payable",
    "profit",
    # 库存：库存、仓库、出入库、调拨、盘点、流水
    "inventory",
    "stock",
    "warehouse",
    "transfer",
    # 成本：成本、单价、计价
    "cost",
    "pricing",
    "valuation",
    # 单据：订单、采购、销售、退货
    "purchase",
    "order",
    "sales",
    "return",
    # 权限与幂等：影响数据可见性与重复提交
    "permission",
    "role",
    "idempotency",
    "data_scope",
    # 公共基础：所有业务都依赖
    "core/exceptions",
    "core/security",
    "utils/bill_no",
)

# ---------------------------------------------------------------------------
# 问题类别定义
# ---------------------------------------------------------------------------
# pylint 消息 ID -> 类别
# 说明：消息 ID 与含义以 pylint 4.1.3 实际定义为准（已用 msgs_store 导出核对），
#       写错映射会让门禁漏报真实缺陷，因此这里只登记语义明确的错误级消息。
PYLINT_CATEGORIES = {
    # 引用类：使用了不存在的名字，运行期必然 NameError
    "E0601": "used-before-assignment",   # 使用前未赋值
    "E0602": "undefined-variable",       # 使用了未定义变量
    "E0603": "undefined-all-variable",   # __all__ 里写了不存在的名字
    "E0606": "possibly-used-before-assignment",  # 条件分支里可能未赋值
    # 调用类：非法调用、参数不匹配
    "E1101": "no-member",                # 属性不存在（空引用/字段缺失）
    "E1102": "not-callable",             # 不可调用
    "E1111": "assignment-from-no-return",  # 无返回值的函数参与赋值
    "E1120": "no-value-for-parameter",   # 缺少必填参数
    "E1121": "too-many-function-args",   # 实参过多
    "E1123": "unexpected-keyword-arg",   # 关键字参数不存在
    "E1125": "missing-kwoa",             # 缺少必填关键字参数
    "E1126": "invalid-sequence-index",   # 下标类型非法
    "E1127": "invalid-slice-index",      # 切片索引非法
    "E1128": "assignment-from-none",     # 对返回 None 的函数结果赋值
    "E1129": "not-context-manager",      # 上下文管理器用法错误
    # 运算符/容器类：类型不匹配，运行期可能算错
    "E1130": "invalid-unary-operand-type",
    "E1131": "unsupported-binary-operation",
    "E1132": "repeated-keyword",         # 同一关键字参数传了两次
    "E1133": "not-an-iterable",          # 对不可迭代值做迭代
    "E1134": "not-a-mapping",            # 对非字典做字典操作
    "E1135": "unsupported-membership-test",  # 不支持 in 判断
    "E1136": "unsubscriptable-object",   # 不可下标
    "E1137": "unsupported-assignment-operation",  # 不支持项赋值
    "E1138": "unsupported-delete-operation",     # 不支持项删除
    "E1139": "invalid-metaclass",
    "E1141": "dict-iter-missing-items",  # 遍历字典时直接解包键
    "E1143": "unhashable-member",        # 不可哈希对象当键
    "E1144": "invalid-slice-step",       # 切片步长为 0
    # 导入类：模块写错名，运行期必然 ImportError
    "E0401": "import-error",
    "E0402": "relative-beyond-top-level",
    # 类定义类：继承、slot、枚举定义错误
    "E0102": "function-redefined",
    "E0110": "abstract-class-instantiated",
    "E0239": "inherit-non-class",
    "E0240": "inconsistent-mro",
    "E0241": "duplicate-bases",
    # 异步类：await 用错位置
    "E1142": "await-outside-async",
    "E1145": "async-context-manager-with-regular-with",
    "E1700": "yield-inside-async-function",
    "E1701": "not-async-context-manager",
    # 解析类：语法错误，无法导入
    "E0001": "syntax-error",
    "F0001": "fatal-error",
    "F0002": "astroid-error",
    "F0010": "syntax-error",
    "F0011": "config-error",
    "F0202": "method-check-failed",
}

# pytype 错误类 -> 类别
PYTYPE_CATEGORIES = {
    "attribute-error": "no-member",              # 属性不存在
    "wrong-arg-count": "too-many-function-args",  # 实参个数不符
    "wrong-arg-types": "bad-arg-type",           # 实参类型不符
    "wrong-keyword-args": "unexpected-keyword-arg",  # 关键字参数不符
    "missing-parameter": "no-value-for-parameter",  # 缺少必填参数
    "not-callable": "not-callable",
    "not-indexable": "unsubscriptable-object",
    "not-instantiable": "not-instantiable",
    "name-error": "undefined-variable",
    "module-attr": "no-member",
    "bad-return-type": "bad-return-type",
    "bad-concrete-type": "bad-concrete-type",
    "unsupported-operands": "unsupported-operands",
    "annotation-type-mismatch": "annotation-mismatch",
    "container-type-mismatch": "container-mismatch",
    "bad-unpacking": "bad-unpacking",
    "import-error": "import-error",
    "not-writable": "unsupported-assignment-operation",
}

# 关键缺陷类别：无论模块风险等级、无论是否存量，一律阻断合并。
# 判定依据：这些问题在 ERP 里会直接算错财务/库存数据或直接崩溃，
#           不能用「存量问题」当理由放过。
CRITICAL_CATEGORIES = {
    "undefined-variable",
    "used-before-assignment",
    "possibly-used-before-assignment",
    "undefined-all-variable",
    "not-callable",
    "no-member",
    "too-many-function-args",
    "unexpected-keyword-arg",
    "no-value-for-parameter",
    "bad-arg-type",
    "missing-parameter",
    "not-an-iterable",
    "not-a-mapping",
    "unsubscriptable-object",
    "unsupported-assignment-operation",
    "unsupported-delete-operation",
    "unsupported-membership-test",
    "unsupported-binary-operation",
    "invalid-unary-operand-type",
    "invalid-sequence-index",
    "invalid-slice-index",
    "invalid-slice-step",
    "unsupported-operands",
    "container-mismatch",
    "annotation-mismatch",
    "not-instantiable",
    "bad-unpacking",
    "repeated-keyword",
    "assignment-from-none",
    "assignment-from-no-return",
    "not-context-manager",
    "await-outside-async",
    "async-context-manager-with-regular-with",
    "yield-inside-async-function",
    "not-async-context-manager",
    "syntax-error",
    "fatal-error",
    "astroid-error",
    "import-error",
    "relative-beyond-top-level",
    "abstract-class-instantiated",
    "inherit-non-class",
    "inconsistent-mro",
    "duplicate-bases",
    "function-redefined",
}

# 只在高风险模块阻断的类别（普通模块仅告警）
HIGH_RISK_ONLY_CATEGORIES = {
    "bad-return-type",
    "bad-concrete-type",
    "invalid-metaclass",
    "dict-iter-missing-items",
    "unhashable-member",
}

# 格式化类：永不阻断，仅提示（可自动修复）
FORMAT_CATEGORIES = {
    "format-error",
    "logging-format-error",
    "stub-error",
    "other",
}


@dataclass
class Finding:
    """单条检查结果。"""

    tool: str  # pylint / pytype
    file: str  # 相对仓库根的文件路径
    line: int
    column: int
    message_id: str  # pylint 的 E1101 / pytype 的 attribute-error
    category: str  # 归一化类别
    message: str
    severity: str  # error / warning / convention / refactor
    is_new: bool = True  # 是否为基线之外的新问题

    @property
    def is_high_risk(self) -> bool:
        """该文件是否属于高风险模块。"""
        return is_high_risk_file(self.file)

    @property
    def is_critical(self) -> bool:
        """是否属于关键缺陷类别（任何模块都必须修）。"""
        return self.category in CRITICAL_CATEGORIES

    @property
    def blocking(self) -> bool:
        """在当前模块风险等级下，该问题是否阻断合并。"""
        if self.is_critical:
            return True  # 关键缺陷不允许被基线豁免
        if not self.is_new:
            return False  # 存量非关键问题不阻断
        if self.category in HIGH_RISK_ONLY_CATEGORIES:
            return self.is_high_risk
        return False

    def to_dict(self) -> dict:
        """转为报告用的字典结构。"""
        return {
            "tool": self.tool,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "messageId": self.message_id,
            "category": self.category,
            "message": self.message,
            "severity": self.severity,
            "isHighRisk": self.is_high_risk,
            "isNew": self.is_new,
            "isCritical": self.is_critical,
            "blocking": self.blocking,
        }


@dataclass
class GateResult:
    """门禁判定结果汇总。"""

    findings: list[Finding] = field(default_factory=list)
    baseline_count: int = 0
    tool_status: dict[str, str] = field(default_factory=dict)  # 各工具执行状态

    @property
    def blocking_findings(self) -> list[Finding]:
        """所有需要阻断合并的问题。"""
        return [f for f in self.findings if f.blocking]

    @property
    def new_findings(self) -> list[Finding]:
        """基线之外的新问题。"""
        return [f for f in self.findings if f.is_new]

    @property
    def baseline_findings(self) -> list[Finding]:
        """命中基线的存量问题。"""
        return [f for f in self.findings if not f.is_new]


class GateError(Exception):
    """检查过程本身出错（缺报告、工具崩溃、配置错误）。

    与「发现问题」区分开：这类情况必须以退出码 2 失败，
    绝不能因为「没解析到问题」就当作通过。
    """


def is_high_risk_file(path: str) -> bool:
    """判断文件是否属于高风险模块（财务/库存/成本等）。"""
    normalized = path.replace("\\", "/").lower()
    return any(pattern in normalized for pattern in HIGH_RISK_PATTERNS)


def fingerprint(finding: Finding) -> str:
    """计算问题指纹，用于与基线比对。

    指纹 = 文件路径 + 消息 ID + 完整消息文本。
    消息文本保持原样（含变量名与数字），避免「不同变量名的同类问题」
    被错误合并成同一条基线记录，导致新缺陷被基线豁免。
    """
    return f"{finding.file}|{finding.message_id}|{finding.message.strip()}"


def load_baseline(baseline_path: Path | None) -> Counter:
    """加载存量基线，返回「指纹 -> 出现次数」的计数。

    计数语义：同一指纹在基线里记录出现过几次，本次出现次数不超过基线次数
    才算存量问题；多出来的次数视为新增问题（同一类问题多了一处）。
    """
    counter: Counter = Counter()
    if baseline_path is None or not baseline_path.exists():
        return counter
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"基线文件无法解析：{baseline_path}（{exc}）") from exc
    for item in data.get("findings", []):
        key = item.get("fingerprint")
        if not key:
            continue
        counter[key] += int(item.get("count", 1))
    return counter


def normalize_path(file_path: str, repo_root: Path, work_dir: Path | None) -> str:
    """把工具输出的文件路径统一成「相对仓库根」的 POSIX 风格路径。

    工具输出的路径基准各不相同：
      - pylint 在 backend/ 下运行时输出 `app/main.py`（相对工作目录）
      - pytype 输出绝对路径 `/workspace/backend/app/main.py`
      - Windows 上 pylint 可能输出 `app\\main.py`（反斜杠）

    统一规则（先判断是否已是仓库根相对路径，再考虑拼接）：
      1. 绝对路径 → 相对仓库根
      2. 路径以「仓库根的相对前缀」开头 → 直接采用（避免 backend/backend/...）
      3. 否则按工具工作目录拼接后再相对仓库根
      4. 最后退回原样（保证不丢信息）
    """
    root = repo_root.resolve()
    normalized = file_path.replace("\\", "/")

    # 1. 绝对路径（含 Windows 盘符，如 C:/erp/backend/app/main.py）
    candidate = Path(file_path)
    if candidate.is_absolute():
        try:
            return str(candidate.resolve().relative_to(root)).replace("\\", "/")
        except (ValueError, OSError):
            return normalized

    # 2. 已是仓库根相对路径：判断其首段是否为仓库根下的顶层目录
    clean = normalized.lstrip("./")
    top_dirs = {p.name for p in root.iterdir() if p.is_dir()} if root.exists() else set()
    first = clean.split("/", 1)[0]
    if first in top_dirs and first not in {".", ""}:
        return clean

    # 3. 按工具工作目录拼接
    if work_dir is not None:
        try:
            resolved = (work_dir / candidate).resolve()
            return str(resolved.relative_to(root)).replace("\\", "/")
        except (ValueError, OSError):
            pass

    # 4. 按仓库根拼接
    try:
        resolved = (root / candidate).resolve()
        return str(resolved.relative_to(root)).replace("\\", "/")
    except (ValueError, OSError):
        return clean


def parse_pylint_json(path: Path, repo_root: Path, work_dir: Path | None = None) -> list[Finding]:
    """解析 pylint 的 JSON 输出。"""
    if not path.exists():
        raise GateError(f"缺少 pylint 报告文件：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateError(f"pylint 报告不是合法 JSON：{path}（{exc}）") from exc
    if not isinstance(data, list):
        raise GateError(f"pylint 报告格式异常，期望数组：{path}")

    findings: list[Finding] = []
    for item in data:
        rel = normalize_path(item.get("path", ""), repo_root, work_dir)
        message_id = item.get("message-id", "")
        findings.append(
            Finding(
                tool="pylint",
                file=rel,
                line=int(item.get("line", 0) or 0),
                column=int(item.get("column", 0) or 0),
                message_id=message_id,
                category=PYLINT_CATEGORIES.get(message_id, "other"),
                message=item.get("message", ""),
                severity=item.get("type", "warning"),
            )
        )
    return findings


def parse_pytype_output(path: Path, repo_root: Path, work_dir: Path | None = None) -> list[Finding]:
    """解析 pytype 的文本输出。

    pytype 的错误行格式为：
        /path/to/file.py:12:5: error: in <func>: 描述 [error-class]
    """
    if not path.exists():
        raise GateError(f"缺少 pytype 输出文件：{path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    findings: list[Finding] = []
    pattern = re.compile(
        r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s*error:\s*(?P<msg>.*?)\s*\[(?P<name>[\w-]+)\]\s*$"
    )
    for raw in text.splitlines():
        match = pattern.match(raw.strip())
        if not match:
            continue
        file_path = match.group("file")
        rel = normalize_path(file_path, repo_root, work_dir)
        name = match.group("name")
        findings.append(
            Finding(
                tool="pytype",
                file=rel,
                line=int(match.group("line")),
                column=int(match.group("col")),
                message_id=name,
                category=PYTYPE_CATEGORIES.get(name, "other"),
                message=match.group("msg").strip(),
                severity="error",
            )
        )
    return findings


def read_scan_status(path: Path) -> dict:
    """读取工具执行清单，校验必需工具是否真的跑成功。"""
    if not path.exists():
        raise GateError(f"缺少检查执行清单：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateError(f"检查执行清单不是合法 JSON：{path}（{exc}）") from exc
    tools = data.get("tools")
    if not isinstance(tools, list) or not tools:
        raise GateError(f"检查执行清单里没有任何工具记录：{path}")
    return data


def verify_tool_status(scan_status: dict, required_tools: list[str]) -> dict[str, str]:
    """校验必需工具是否执行成功，返回「工具名 -> 状态」。

    任何必需工具缺失或状态为 failed，都判定为检查失败（退出码 2），
    绝不能因为「没解析到问题」就把门禁判成通过。
    """
    recorded: dict[str, dict] = {}
    for item in scan_status.get("tools", []):
        recorded[str(item.get("tool", ""))] = item

    status: dict[str, str] = {}
    problems: list[str] = []
    for tool in required_tools:
        item = recorded.get(tool)
        if item is None:
            problems.append(f"工具 {tool} 未出现在执行清单中")
            status[tool] = "missing"
            continue
        tool_status = str(item.get("status", ""))
        status[tool] = tool_status
        if tool_status != "completed":
            reason = item.get("failure") or f"状态 {tool_status}"
            problems.append(f"工具 {tool} 执行失败：{reason}")
    if problems:
        raise GateError("；".join(problems))
    return status


def evaluate(findings: list[Finding], baseline: Counter) -> GateResult:
    """标记新增问题并汇总结果。

    判定顺序：先按指纹消耗基线额度，超出额度的部分算新增；
    关键缺陷类别一律视为阻断（见 Finding.blocking）。
    """
    used: Counter = Counter()
    for finding in findings:
        key = fingerprint(finding)
        if baseline.get(key, 0) > used[key]:
            used[key] += 1
            finding.is_new = False
        else:
            finding.is_new = True
    return GateResult(findings=findings, baseline_count=sum(baseline.values()))


def write_json_report(result: GateResult, output_path: Path, meta: dict) -> None:
    """输出 JSON 报告，供效能看板采集。"""
    payload = {
        "schemaVersion": "2.0",
        "meta": meta,
        "summary": {
            "total": len(result.findings),
            "new": len(result.new_findings),
            "baseline": len(result.baseline_findings),
            "blocking": len(result.blocking_findings),
            "critical": len([f for f in result.findings if f.is_critical]),
            "highRiskBlocking": len(
                [f for f in result.blocking_findings if f.is_high_risk]
            ),
            "byCategory": _count_by(result.findings, "category"),
            "byTool": _count_by(result.findings, "tool"),
        },
        "tools": result.tool_status,
        "findings": [f.to_dict() for f in result.findings],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _count_by(findings: list[Finding], attr: str) -> dict[str, int]:
    """按指定属性统计数量，降序返回。"""
    counter: dict[str, int] = {}
    for item in findings:
        key = getattr(item, attr)
        counter[key] = counter.get(key, 0) + 1
    return dict(sorted(counter.items(), key=lambda kv: -kv[1]))


def write_markdown_report(result: GateResult, output_path: Path, meta: dict) -> None:
    """输出 Markdown 报告，便于在 MR / 流水线日志中直接阅读。"""
    blocking = result.blocking_findings
    lines = [
        "# 代码质量门禁报告",
        "",
        f"- 分支：{meta.get('branch', '-')}",
        f"- 提交：{meta.get('commit', '-')}",
        f"- 检查范围：{meta.get('scope', '-')}",
        f"- 工具状态：{meta.get('toolStatus', '-')}",
        f"- 问题总数：{len(result.findings)}（新增 {len(result.new_findings)}，"
        f"存量 {len(result.baseline_findings)}）",
        f"- **阻断问题：{len(blocking)}**",
        "",
    ]

    if blocking:
        lines.append("## 阻断项（必须修复后才能合并）")
        lines.append("")
        lines.append("| 工具 | 风险 | 关键 | 文件 | 行 | 问题 |")
        lines.append("|------|------|------|------|----|------|")
        for item in blocking:
            risk = "高" if item.is_high_risk else "普通"
            critical = "是" if item.is_critical else "否"
            lines.append(
                f"| {item.tool} | {risk} | {critical} | `{item.file}` | {item.line} | "
                f"[{item.message_id}] {item.message} |"
            )
        lines.append("")
    else:
        lines.append("## 阻断项")
        lines.append("")
        lines.append("无阻断问题 ✓")
        lines.append("")

    new_non_blocking = [f for f in result.new_findings if not f.blocking]
    if new_non_blocking:
        lines.append("## 新增告警（不阻断，建议尽快处理）")
        lines.append("")
        lines.append("| 工具 | 风险 | 文件 | 行 | 问题 |")
        lines.append("|------|------|------|----|------|")
        for item in new_non_blocking[:50]:
            risk = "高" if item.is_high_risk else "普通"
            lines.append(
                f"| {item.tool} | {risk} | `{item.file}` | {item.line} | "
                f"[{item.message_id}] {item.message} |"
            )
        if len(new_non_blocking) > 50:
            lines.append(f"| ... | | | | 其余 {len(new_non_blocking) - 50} 条见 JSON 报告 |")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_baseline(result: GateResult, output_path: Path, meta: dict) -> None:
    """写出现状基线文件，供后续比对使用。

    规则：
      - 只收录非关键缺陷（关键缺陷必须修，不允许挂基线）
      - 按指纹统计出现次数，避免同一类问题多了一处时仍被豁免
    """
    counts: Counter = Counter()
    samples: dict[str, Finding] = {}
    for item in result.findings:
        if item.is_critical:
            continue  # 关键缺陷不进基线，必须修
        key = fingerprint(item)
        counts[key] += 1
        samples.setdefault(key, item)

    entries = []
    for key, count in counts.most_common():
        item = samples[key]
        entries.append(
            {
                "fingerprint": key,
                "file": item.file,
                "messageId": item.message_id,
                "category": item.category,
                "message": item.message,
                "count": count,
            }
        )
    payload = {
        "schemaVersion": "2.0",
        "generatedAt": meta.get("timestamp", ""),
        "description": (
            "存量代码问题基线。基线内的问题不阻断合并，但会在报告中提示；"
            "基线之外的新问题按门禁分级策略判定（阻断 / 告警）。"
            "关键缺陷（空引用、类型不匹配等）不收录进基线，必须修复；"
            "count 记录同一指纹的出现次数，用于识别「同类问题多了一处」。"
        ),
        "findings": entries,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_meta(scope: str, tool_status: dict[str, str]) -> dict:
    """收集本次判定的元信息，供报告与看板使用。"""
    return {
        "branch": os.environ.get("CI_COMMIT_BRANCH", os.environ.get("GITHUB_REF_NAME", "")),
        "commit": os.environ.get("CI_COMMIT_SHA", os.environ.get("GITHUB_SHA", "")),
        "mergeRequest": os.environ.get("CI_MERGE_REQUEST_IID", ""),
        "pipeline": os.environ.get("CI_PIPELINE_ID", os.environ.get("GITHUB_RUN_ID", "")),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "toolStatus": ",".join(f"{k}:{v}" for k, v in sorted(tool_status.items())) or "-",
    }


def main() -> int:
    """命令行入口：解析参数、执行判定、输出报告，返回退出码。

    退出码：0 通过 / 1 有阻断问题 / 2 检查执行失败。
    """
    parser = argparse.ArgumentParser(description="代码质量门禁判定")
    parser.add_argument("--scan-status", type=Path, help="工具执行清单 scan-status.json")
    parser.add_argument("--pylint-json", type=Path, help="pylint 的 JSON 输出文件")
    parser.add_argument("--pytype-output", type=Path, help="pytype 的文本输出文件")
    parser.add_argument("--baseline", type=Path, help="存量基线文件")
    parser.add_argument("--update-baseline", action="store_true", help="更新基线文件")
    parser.add_argument("--output", type=Path, default=Path("reports/quality"))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--required-tools",
        default="pylint,pytype",
        help="必须执行成功的工具，逗号分隔",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="运行 pylint/pytype 时的工作目录，其输出路径相对该目录",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    work_dir = args.work_dir.resolve() if args.work_dir else None
    required_tools = [item for item in args.required_tools.split(",") if item]

    try:
        # 1. 先确认工具真的跑成功，避免「没跑检查」被当成「没有问题」
        tool_status: dict[str, str] = {}
        if args.scan_status:
            scan_status = read_scan_status(args.scan_status)
            tool_status = verify_tool_status(scan_status, required_tools)
        elif required_tools:
            raise GateError("缺少 --scan-status，无法确认检查是否真的执行成功")

        # 2. 解析各工具报告；缺文件/格式错都视为检查失败
        findings: list[Finding] = []
        if "pylint" in required_tools:
            if not args.pylint_json:
                raise GateError("缺少 --pylint-json")
            findings.extend(parse_pylint_json(args.pylint_json, repo_root, work_dir))
        if "pytype" in required_tools:
            if not args.pytype_output:
                raise GateError("缺少 --pytype-output")
            findings.extend(parse_pytype_output(args.pytype_output, repo_root, work_dir))

        # 3. 与基线比对并分级
        baseline = load_baseline(args.baseline)
        result = evaluate(findings, baseline)
        result.tool_status = tool_status
    except GateError as exc:
        # 检查执行失败：退出码 2，不允许因为「没解析到问题」而放行
        print(f"代码质量检查执行失败：{exc}", file=sys.stderr)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "quality-report.md").write_text(
            f"# 代码质量门禁报告\n\n**检查执行失败，门禁未通过。**\n\n原因：{exc}\n",
            encoding="utf-8",
        )
        return 2

    meta = build_meta(args.scan_status.name if args.scan_status else "backend/app", tool_status)
    write_json_report(result, args.output / "quality-report.json", meta)
    write_markdown_report(result, args.output / "quality-report.md", meta)

    blocking = result.blocking_findings
    critical = [f for f in result.findings if f.is_critical]
    print(f"问题总数 {len(result.findings)}，新增 {len(result.new_findings)}，阻断 {len(blocking)}")

    if args.update_baseline and args.baseline:
        if blocking:
            # 有阻断问题时不允许刷新基线，否则会把新缺陷写成存量
            print("存在阻断问题，拒绝更新基线", file=sys.stderr)
            return 1
        write_baseline(result, args.baseline, meta)
        print(f"基线已更新: {args.baseline}")

    for item in blocking[:20]:
        risk = "高风险" if item.is_high_risk else "普通"
        print(f"  [阻断][{risk}] {item.file}:{item.line} [{item.message_id}] {item.message}")

    if blocking:
        print(f"\n门禁未通过：{len(blocking)} 个阻断问题（其中关键缺陷 {len(critical)} 个）")
        return 1
    print("\n门禁通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
