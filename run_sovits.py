"""
单独启动 / 停止语音引擎服务（命令行工具）。

平时用不到这个文件 —— 正常使用直接在网页界面上点「🚀 启动服务」就行。
它的用途是"界面打不开时，单独把语音服务跑起来排查问题"。

用法：
    python run_sovits.py          启动服务（在前台运行，能直接看到日志）
    python run_sovits.py --stop   停止服务（结束占用端口的进程）
"""

import socket
import subprocess
import sys

import config

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def port_in_use(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


def stop_service() -> int:
    """结束占用语音服务端口的进程。"""
    if not port_in_use(config.SOVITS_PORT):
        print(f"端口 {config.SOVITS_PORT} 上没有服务在运行，不用停止。")
        return 0

    print(f"正在停止端口 {config.SOVITS_PORT} 上的服务…")
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
    except Exception as e:
        print(f"执行 netstat 失败：{e}")
        return 1

    pids = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" \
                and parts[1].endswith(f":{config.SOVITS_PORT}"):
            pids.add(parts[4])

    if not pids:
        print("没找到对应的进程。")
        return 1

    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        print(f"已结束进程 PID={pid}")

    print("完成。")
    return 0


def start_service() -> int:
    """启动语音引擎服务（前台运行，Ctrl+C 可停止）。"""
    missing = []
    for path, label in [
        (config.GPT_SOVITS_PYTHON, "引擎 Python"),
        (config.GPT_SOVITS_API, "服务脚本 api.py"),
        (config.SOVITS_WEIGHT, "声学模型"),
        (config.GPT_WEIGHT, "语气模型"),
        (config.REF_AUDIO, "参考音频"),
    ]:
        if not path.exists():
            missing.append(f"{label}：{path}")

    if missing:
        print("缺少必要的文件，无法启动：")
        for m in missing:
            print(f"  - {m}")
        print("\n请先运行环境自检：python check_env.py")
        return 1

    if port_in_use(config.SOVITS_PORT):
        print(f"端口 {config.SOVITS_PORT} 已被占用。")
        print("如果是上次没关干净的服务，先执行：python run_sovits.py --stop")
        return 1

    args = [
        str(config.GPT_SOVITS_PYTHON),
        str(config.GPT_SOVITS_API),
        "-d", "cuda",
        "-a", "127.0.0.1",
        "-p", str(config.SOVITS_PORT),
        "-s", str(config.SOVITS_WEIGHT),
        "-g", str(config.GPT_WEIGHT),
        "-dr", str(config.REF_AUDIO),
        "-dt", config.REF_TEXT,
        "-dl", config.REF_LANG,
    ]

    print("=" * 64)
    print("正在启动语音引擎服务…")
    print(f"  声学模型：{config.SOVITS_WEIGHT.name}")
    print(f"  语气模型：{config.GPT_WEIGHT.name}")
    print(f"  参考音频：{config.REF_AUDIO.name}")
    print(f"  监听地址：http://127.0.0.1:{config.SOVITS_PORT}")
    print("=" * 64)
    print("模型加载需要 10-30 秒，看到 'Application startup complete' 就是好了。")
    print("按 Ctrl+C 可以停止。")
    print()

    try:
        return subprocess.call(args, cwd=str(config.GPT_SOVITS_DIR))
    except KeyboardInterrupt:
        print("\n已停止。")
        return 0


if __name__ == "__main__":
    if "--stop" in sys.argv:
        sys.exit(stop_service())
    sys.exit(start_service())
