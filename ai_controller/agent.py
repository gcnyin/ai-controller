"""Agent 调用模块 —— Agent 配置与 call_agent 函数。"""

import re
import time
import shlex
import subprocess


# ─── Agent 配置 ────────────────────────────────────────────────────────

AGENTS = {
    "pi": {
        "cmd": "pi",
        "args": ["-p"],            # -p = non-interactive, print & exit
        "cwd_option": None,        # runs in cwd
    },
    "opencode": {
        "cmd": "opencode",
        "args": ["run"],
        "cwd_option": "--dir",
    },
    "claude": {
        "cmd": "claude",
        "args": ["-p", "--dangerously-skip-permissions"],
        "cwd_option": None,
    },
    "codex": {
        "cmd": "codex",
        "args": ["exec", "--full-auto"],
        "cwd_option": "-C",
    },
}


def build_agent_command(agent: str, prompt: str, target_dir: str,
                       extra_args: list | None = None) -> tuple[list[str], str | None]:
    """构建 Agent 命令行，返回 (命令列表, cwd)。

    call_agent 和 _build_dry_run_command 共用此函数，
    避免命令拼接逻辑在两处重复。

    Returns:
        (cmd_parts, cwd) — cwd 为 None 时表示 Agent 自带工作目录选项，
        不需要额外设置 subprocess cwd。
    """
    cfg = AGENTS[agent]

    cmd_parts = [cfg["cmd"]]
    if extra_args:
        cmd_parts.extend(extra_args)
    cmd_parts.extend(cfg["args"])

    if cfg["cwd_option"]:
        cmd_parts.extend([cfg["cwd_option"], target_dir])
        cwd = None
    else:
        cwd = target_dir

    cmd_parts.append(prompt)
    return cmd_parts, cwd


def parse_summary(output: str) -> str:
    """从 agent 输出中提取 SUMMARY 行"""
    # 匹配 SUMMARY: xxx 或 SUMMARY：xxx（中英文冒号都支持）
    m = re.search(r"SUMMARY[:：]\s*(.+)", output, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # 如果没有 SUMMARY 行，尝试用 git diff 的简短描述
    return "未提供具体说明"


def call_agent(agent: str, prompt: str, target_dir: str,
               timeout: int = 600,
               extra_args: list | None = None,
               quiet: bool = False) -> tuple[bool, str, str, float]:
    """
    调用 agent 进行一轮修改。
    返回 (success, summary, raw_output, elapsed_seconds)

    quiet=True 时不打印 agent 的原始输出（不打印 prompt 和冗余输出）。
    """
    cmd_parts, cwd = build_agent_command(
        agent, prompt, target_dir, extra_args,
    )

    if not quiet:
        print(f"  🚀 执行: {' '.join(shlex.quote(str(p)) for p in cmd_parts[:4])} ...")
    else:
        print(f"  🚀 {agent} 工作中...")

    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd_parts,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        stdout_data, _ = proc.communicate(timeout=timeout)
        elapsed = time.time() - start

        # 静默模式不打印原始输出，只显示最后几行摘要
        if not quiet:
            if stdout_data:
                print(stdout_data, end="", flush=True)
        else:
            # 静默模式下只显示最后 5 行，避免刷屏
            if stdout_data:
                lines = stdout_data.strip().split('\n')
                tail = lines[-5:] if len(lines) > 5 else lines
                if tail:
                    print("\n".join(tail))

        summary = parse_summary(stdout_data)
        if proc.returncode != 0 and "未提供具体说明" in summary:
            summary = f"Agent 异常退出（返回码 {proc.returncode}），未提供改动说明"
        return proc.returncode == 0, summary, stdout_data, elapsed

    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            partial_stdout, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            partial_stdout = ""
        elapsed = time.time() - start
        if not quiet and partial_stdout:
            print(partial_stdout, end="", flush=True)
        print(f"\n  Agent 超时（{timeout} 秒）")
        return False, "Agent 执行超时", partial_stdout, elapsed
    except KeyboardInterrupt:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    except Exception as e:
        elapsed = time.time() - start
        print(f"  Agent 调用失败: {e}")
        return False, f"调用失败: {e}", "", elapsed


def run_test_command(cmd: str, target_dir: str, timeout: int) -> tuple[bool, str]:
    """执行测试命令，返回 (passed, output)。

    Args:
        cmd: 要执行的 shell 命令
        target_dir: 工作目录
        timeout: 超时秒数

    Returns:
        (passed, output): passed 为 True 表示测试通过（退出码 0），
        output 为合并的 stdout + stderr。
    """
    print(f"  🧪 运行测试: {cmd}")
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=target_dir,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.time() - start
        output = proc.stdout
        if proc.stderr:
            output += "\n" + proc.stderr
        output = output.strip()
        passed = proc.returncode == 0
        if passed:
            print(f"  ✓ 测试通过 ({elapsed:.1f}s)")
        else:
            print(f"  ✗ 测试失败 (退出码 {proc.returncode}, {elapsed:.1f}s)")
            # 打印测试输出的尾部方便快速定位
            if output:
                lines = output.split('\n')
                tail = lines[-10:] if len(lines) > 10 else lines
                print("  ── 测试输出尾部 ──")
                for line in tail:
                    print(f"  {line}")
                print("  ── 结束 ──")
        return passed, output
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"  ✗ 测试超时 ({timeout}s)")
        return False, f"测试命令超时（{timeout} 秒）"
    except Exception as e:
        elapsed = time.time() - start
        print(f"  ✗ 测试执行异常: {e}")
        return False, f"测试命令执行异常: {e}"
