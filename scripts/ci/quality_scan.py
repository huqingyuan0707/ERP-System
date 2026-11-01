"""代码质量检查执行器。

职责：真实地运行 pyink / pylint / pytype 三件套，采集每个工具的
「退出码 + 输出文件 + 失败原因」，写入 scan-status.json 清单，
供 quality_gate.py 做分级判定。

设计要点（与 quality_gate.py 的契约）：
  1. 失败不伪装成功：工具崩溃、版本不符、超时都会在清单里记为 failed，
     并让本脚本以退出码 2 结束（区别于「工具跑完但发现问题」的退出码 1）。
  2. 退出码语义：
     0 = 检查全部执行完毕且门禁通过
     1 = 检查执行完毕，但存在阻断问题
     2 = 检查执行过程本身失败（工具缺失、崩溃、超时、报告写不出来）
  3. 不使用 shell=True，也不使用 `|| true` 之类吞掉退出码的写法，
     全部通过 subprocess.run 直接拿真实 returncode。

用法（CI 全量）：
  python scripts/ci/quality_scan.py --repo-root . --reports backend/reports \
      --tools pyink pylint pytype
用法（本地按改动文件快检）：
  python scripts/ci/quality_scan.py --repo-root . --local backend/app/api/v1/endpoints/goods.py
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# 让日志统一输出到 stderr，不污染报告文件内容
logger = logging.getLogger("quality_scan")

# 固定工具版本，保证本地与 CI 判定标准一致（版本漂移会让基线失效）
REQUIRED_VERSIONS = {
    "pyink": "26.5.1",
    "pylint": "4.1.3",
    "pytype": "2024.10.11",
}

# 单个工具的超时时间（秒）：pytype 全量分析最慢，给足 20 分钟
TOOL_TIMEOUT_SECONDS = 1200


@dataclass
class ToolResult:
    """单个工具的执行结果。"""

    tool: str  # 工具名：pyink / pylint / pytype
    version: str  # 实际安装版本
    command: list[str] = field(default_factory=list)  # 实际执行的命令行
    targets: list[str] = field(default_factory=list)  # 检查目标
    return_code: int | None = None  # 进程真实退出码；未启动成功时为 None
    status: str = "failed"  # completed（进程跑完）/ failed（执行过程失败）
    failure: str = ""  # 失败原因说明，成功时为空字符串

    def to_dict(self) -> dict[str, object]:
        """转成 JSON 可序列化的字典。"""
        return {
            "tool": self.tool,
            "version": self.version,
            "command": self.command,
            "targets": self.targets,
            "returnCode": self.return_code,
            "status": self.status,
            "failure": self.failure,
        }


def utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def detect_version(tool: str) -> str:
    """获取工具版本号，取不到时返回空字符串。"""
    command = [sys.executable, "-m", tool, "--version"]
    try:
        completed = subprocess.run(  # noqa: S603 - 命令参数固定，无 shell 注入
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # 工具未安装或启动失败
        logger.warning("无法获取 %s 版本: %s", tool, exc)
        return ""
    output = (completed.stdout or "") + (completed.stderr or "")
    for line in output.splitlines():
        if tool not in line:
            continue
        for token in line.replace(",", " ").split():
            if token and token[0].isdigit():
                # 取行中第一个以数字开头的片段，例如 "pylint 4.1.3"
                return token
    return ""


def run_tool(
    tool: str,
    command: list[str],
    targets: list[str],
    work_dir: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> ToolResult:
    """执行单个工具，把标准输出与错误输出分别落盘，返回执行结果。"""
    result = ToolResult(
        tool=tool,
        version=detect_version(tool),
        command=command,
        targets=targets,
    )
    expected = REQUIRED_VERSIONS.get(tool, "")
    if expected and result.version and result.version != expected:
        # 版本不一致会让基线比对失真，直接判失败而不是继续跑
        result.failure = f"版本不符：期望 {expected}，实际 {result.version}"
        logger.error("[%s] %s", tool, result.failure)
        return result

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(  # noqa: S603 - 命令参数由本脚本构造，无 shell 注入
            command,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TOOL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result.failure = f"执行超时（超过 {TOOL_TIMEOUT_SECONDS} 秒）"
        result.status = "failed"
        logger.error("[%s] %s", tool, result.failure)
        return result
    except (OSError, subprocess.SubprocessError) as exc:
        result.failure = f"执行失败：{exc}"
        result.status = "failed"
        logger.error("[%s] %s", tool, result.failure)
        return result

    # 落盘输出，供门禁脚本解析
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    result.return_code = completed.returncode
    result.status = "completed"  # 进程确实跑完了，退出码由门禁脚本解释
    logger.info("[%s] 退出码 %s", tool, completed.returncode)
    return result


def build_commands(tools: list[str], targets: list[str]) -> dict[str, tuple[list[str], str]]:
    """构造各工具的命令行，值为 (命令行, 主输出文件名)。"""
    commands: dict[str, tuple[list[str], str]] = {}
    if "pyink" in tools:
        # pyink 不会自动发现独立配置文件，必须显式 --config，且文件需带 [tool.pyink] 段
        commands["pyink"] = (
            [
                sys.executable,
                "-m",
                "pyink",
                "--config=pyink.toml",
                "--check",
                "--diff",
                *targets,
            ],
            "pyink.txt",
        )
    if "pylint" in tools:
        # pylint 的 JSON 走 stdout，便于直接解析
        commands["pylint"] = (
            [
                sys.executable,
                "-m",
                "pylint",
                "--rcfile=pylintrc",
                "--output-format=json",
                *targets,
            ],
            "pylint.json",
        )
    if "pytype" in tools:
        # pytype 是模块入口，输出走 stdout/stderr
        commands["pytype"] = (
            [sys.executable, "-m", "pytype", "--config=pytype.cfg", *targets],
            "pytype.txt",
        )
    return commands


def run_gate(
    repo_root: Path,
    backend_dir: Path,
    reports_dir: Path,
    baseline: Path,
    required_tools: list[str],
) -> int:
    """调用 quality_gate.py 做分级判定，返回门禁退出码。"""
    gate_script = Path(__file__).with_name("quality_gate.py")
    command = [
        sys.executable,
        str(gate_script),
        "--scan-status",
        str(reports_dir / "scan-status.json"),
        "--pylint-json",
        str(reports_dir / "pylint.json"),
        "--pytype-output",
        str(reports_dir / "pytype.txt"),
        "--baseline",
        str(baseline),
        "--output",
        str(reports_dir / "quality"),
        "--repo-root",
        str(repo_root),
        "--work-dir",
        str(backend_dir),
        "--required-tools",
        ",".join(required_tools),
    ]
    logger.info("执行门禁判定")
    completed = subprocess.run(  # noqa: S603 - 参数固定，无 shell 注入
        command,
        cwd=str(repo_root),
        check=False,
    )
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    """命令行入口：跑检查、落盘清单、调用门禁判定，返回退出码。"""
    parser = argparse.ArgumentParser(description="代码质量检查执行器")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="仓库根目录")
    parser.add_argument("--reports", type=Path, default=None, help="报告输出目录")
    parser.add_argument(
        "--tools",
        nargs="+",
        choices=["pyink", "pylint", "pytype"],
        default=["pyink", "pylint", "pytype"],
        help="要执行的检查工具",
    )
    parser.add_argument(
        "--local",
        nargs="*",
        default=None,
        help="本地快检模式：只跑 pylint，且只检查给定的仓库相对路径",
    )
    parser.add_argument("--baseline", type=Path, default=None, help="存量基线文件")
    parser.add_argument(
        "--update-baseline", action="store_true", help="门禁通过后更新存量基线"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    repo_root = args.repo_root.resolve()
    backend_dir = repo_root / "backend"
    reports_dir = (args.reports or (backend_dir / "reports")).resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)
    baseline = args.baseline or (backend_dir / "quality_baseline.json")

    local_mode = args.local is not None
    if local_mode:
        # 本地快检：pytype 在 Windows 不受支持，pyink 改动自动修复无需在钩子里跑
        tools = ["pylint"]
        targets: list[str] = []
        for item in args.local or []:
            rel = Path(item)
            try:
                rel_to_backend = rel.resolve().relative_to(backend_dir)
            except ValueError:
                logger.warning("跳过不在 backend 下的文件: %s", item)
                continue
            targets.append(rel_to_backend.as_posix())
        if not targets:
            logger.info("没有需要检查的文件，结束")
            return 0
    else:
        tools = list(args.tools)
        targets = ["app"]

    commands = build_commands(tools, targets)
    results: list[ToolResult] = []
    execution_failed = False
    for tool, (command, filename) in commands.items():
        stdout_path = reports_dir / filename
        stderr_path = reports_dir / f"{tool}.stderr.txt"
        result = run_tool(tool, command, targets, backend_dir, stdout_path, stderr_path)
        results.append(result)
        if result.status == "failed":
            execution_failed = True

    manifest = {
        "schemaVersion": "2.0",
        "scope": "backend/app" if not local_mode else ",".join(targets),
        "fullScan": targets == ["app"],
        "startedAt": utc_now_iso(),
        "finishedAt": utc_now_iso(),
        "tools": [item.to_dict() for item in results],
    }
    status_path = reports_dir / "scan-status.json"
    status_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("检查清单已写入 %s", status_path)

    if execution_failed:
        # 检查过程本身失败：工具没跑成功，不能据此判定门禁通过
        logger.error("检查执行失败，终止门禁判定")
        return 2

    gate_code = run_gate(repo_root, backend_dir, reports_dir, baseline, tools)
    if args.update_baseline and gate_code == 0:
        # 只有门禁通过时才允许刷新基线，避免把新问题写成存量
        update_command = [
            sys.executable,
            str(Path(__file__).with_name("quality_gate.py")),
            "--scan-status",
            str(status_path),
            "--pylint-json",
            str(reports_dir / "pylint.json"),
            "--pytype-output",
            str(reports_dir / "pytype.txt"),
            "--baseline",
            str(baseline),
            "--update-baseline",
            "--output",
            str(reports_dir / "quality"),
            "--repo-root",
            str(repo_root),
            "--work-dir",
            str(backend_dir),
            "--required-tools",
            ",".join(tools),
        ]
        subprocess.run(update_command, cwd=str(repo_root), check=False)  # noqa: S603
    return gate_code


if __name__ == "__main__":
    sys.exit(main())
