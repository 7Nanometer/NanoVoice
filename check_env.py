"""
环境自检 —— 一键体检，看这台电脑能不能跑起这套系统。

什么时候用：
    · 换了一台电脑，部署完之后
    · 程序跑不起来，想快速定位是哪一环出了问题
    · 答辩演示前，确认一切正常

用法：
    在项目目录下执行：  python check_env.py
    或者直接双击 start.bat（它会先跑一遍自检）
"""

import importlib
import socket
import subprocess
import sys
from pathlib import Path

# 让这个脚本自己的输出能正常显示中文和符号
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent

# 统计结果
passed = 0
failed = 0
warned = 0


def ok(msg):
    global passed
    passed += 1
    print(f"  [通过] {msg}")


def fail(msg, how_to_fix=""):
    global failed
    failed += 1
    print(f"  [失败] {msg}")
    if how_to_fix:
        print(f"         -> {how_to_fix}")


def warn(msg):
    global warned
    warned += 1
    print(f"  [注意] {msg}")


def section(title):
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def port_in_use(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


def main():
    print()
    print("+" + "-" * 62 + "+")
    print("|" + "系统环境自检".center(52) + "|")
    print("+" + "-" * 62 + "+")

    # ---------- 1. Python ----------
    section("1. Python 运行环境")
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 10):
        ok(f"Python 版本 {sys.version.split()[0]}")
    else:
        fail(f"Python 版本过低（{sys.version.split()[0]}）",
             "需要 3.10 或更高版本，去 python.org 下载新版安装")

    print(f"         解释器位置：{sys.executable}")

    # ---------- 2. 依赖库 ----------
    section("2. 依赖库")
    # (import名字, 显示名, 是否必需)
    deps = [
        ("fastapi", "fastapi（新版界面后端）", True),
        ("uvicorn", "uvicorn（新版界面服务器）", True),
        ("pydub", "pydub（音频拼接）", True),
        ("audioop", "audioop（音频运算，Python3.13需要单独装 audioop-lts）", True),
        ("edge_tts", "edge-tts（微软在线语音）", True),
        ("requests", "requests（网络请求）", True),
        ("bs4", "beautifulsoup4（网页解析）", True),
        ("openai", "openai（大模型接口）", True),
        ("dotenv", "python-dotenv（读取配置文件）", True),
        ("gradio", "gradio（旧版界面，备用）", True),
    ]
    for mod_name, label, required in deps:
        try:
            m = importlib.import_module(mod_name)
            version = getattr(m, "__version__", "")
            ok(f"{label}" + (f"  版本 {version}" if version else ""))
        except ImportError:
            if required:
                fail(f"缺少 {label}", "执行：pip install -r requirements.txt")
            else:
                warn(f"缺少 {label}（不影响主要功能）")

    # ---------- 3. ffmpeg ----------
    section("3. ffmpeg（音频处理工具）")
    try:
        import config
        ffmpeg = Path(config.FFMPEG)
        if ffmpeg.exists():
            size_mb = ffmpeg.stat().st_size / 1024 / 1024
            ok(f"ffmpeg 可用：{ffmpeg}（{size_mb:.0f} MB）")
        elif str(ffmpeg) == "ffmpeg":
            fail("系统里没找到 ffmpeg",
                 "正常情况下语音引擎会自带一个；如果项目里的 GPT-SoVITS 目录不完整，请重新搬运")
        else:
            fail(f"ffmpeg 路径存在但文件不见了：{ffmpeg}")
    except Exception as e:
        fail(f"无法读取配置：{e}")

    # ---------- 4. GPT-SoVITS 语音引擎 ----------
    section("4. GPT-SoVITS 语音引擎")
    try:
        import config

        if config.GPT_SOVITS_DIR.exists():
            ok(f"引擎目录：{config.GPT_SOVITS_DIR}")
        else:
            fail(f"引擎目录不存在：{config.GPT_SOVITS_DIR}",
                 "双击 copy_sovits.bat 从原位置搬运过来")

        files = [
            (config.GPT_SOVITS_PYTHON, "引擎自带的 Python"),
            (config.GPT_SOVITS_API, "服务脚本 api.py"),
            (config.SOVITS_WEIGHT, "声学模型（决定音色）"),
            (config.GPT_WEIGHT, "语气模型（决定语调）"),
            (config.REF_AUDIO, "参考音频"),
        ]
        for path, label in files:
            if path.exists():
                size_mb = path.stat().st_size / 1024 / 1024
                ok(f"{label}：{Path(path).name}（{size_mb:.1f} MB）")
            else:
                fail(f"{label} 不存在：{path}")

    except Exception as e:
        fail(f"检查引擎时出错：{e}")

    # ---------- 5. 显卡 ----------
    section("5. 显卡（语音克隆需要）")
    try:
        import config
        if config.GPT_SOVITS_PYTHON.exists():
            result = subprocess.run(
                [str(config.GPT_SOVITS_PYTHON), "-c",
                 "import torch;"
                 "print(torch.cuda.is_available());"
                 "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '');"
                 "print(f'{torch.cuda.get_device_properties(0).total_memory/1024**3:.1f}' if torch.cuda.is_available() else '')"],
                capture_output=True, text=True, timeout=180,
            )
            lines = [x.strip() for x in result.stdout.strip().splitlines()]
            if len(lines) >= 1 and lines[0] == "True":
                name = lines[1] if len(lines) > 1 else "未知型号"
                mem = lines[2] if len(lines) > 2 else "?"
                ok(f"显卡可用：{name}（显存 {mem} GB）")
                if mem != "?" and float(mem) < 6:
                    warn("显存偏小（建议 6GB 以上），生成长文案时可能变慢或失败")
            else:
                fail("显卡不可用（torch 检测不到 CUDA）",
                     "语音克隆会退化成 CPU 运算，速度极慢；请检查显卡驱动是否安装")
        else:
            warn("引擎的 Python 不存在，跳过显卡检查")
    except subprocess.TimeoutExpired:
        warn("显卡检查超时（首次加载 torch 比较慢，可以重跑一次）")
    except Exception as e:
        warn(f"显卡检查没能完成：{e}")

    # ---------- 6. 配置文件 ----------
    section("6. 密钥配置（.env）")
    env_file = ROOT / ".env"
    if not env_file.exists():
        fail("没有找到 .env 文件",
             "把 .env.example 复制一份改名为 .env，然后填上你的密钥")
    else:
        ok(f"配置文件存在：{env_file}")
        content = env_file.read_text(encoding="utf-8", errors="replace")
        key = ""
        for line in content.splitlines():
            if line.strip().startswith("OPENAI_API_KEY="):
                key = line.split("=", 1)[1].strip()
                break

        if not key or key in ("your_openai_api_key_here", "在这里填你的密钥"):
            fail("密钥还没填", "打开 .env 文件，在 OPENAI_API_KEY= 后面填上你的密钥")
        elif len(key) < 12:
            warn(f"密钥看起来偏短：{key[:4]}...{key[-4:]}（请确认是否完整）")
        else:
            # 只显示前后各 4 位，中间用星号遮住，避免日志泄露密钥
            ok(f"密钥已填写：{key[:4]}{'*' * 8}{key[-4:]}")

    # ---------- 7. 端口 ----------
    section("7. 端口占用情况")
    try:
        import config
        if port_in_use(config.SOVITS_PORT):
            ok(f"端口 {config.SOVITS_PORT}（语音服务）：有服务在运行")
        else:
            print(f"  [提示] 端口 {config.SOVITS_PORT}（语音服务）：空闲"
                  f"（正常，点界面上的「启动服务」即可）")

        if port_in_use(config.APP_PORT):
            warn(f"端口 {config.APP_PORT}（网页界面）已被占用，"
                 f"启动时可能失败；请先关掉已在运行的窗口")
        else:
            ok(f"端口 {config.APP_PORT}（网页界面）：空闲")
    except Exception as e:
        warn(f"端口检查出错：{e}")

    # ---------- 8. 输出目录 ----------
    section("8. 输出目录可写")
    try:
        import config
        probe = config.AUDIO_DIR / "_写入测试.tmp"
        probe.write_text("test", encoding="utf-8")
        probe.unlink()
        ok(f"输出目录可写：{config.AUDIO_DIR}")
    except Exception as e:
        fail(f"输出目录无法写入：{e}", "检查磁盘空间和文件夹权限")

    # ---------- 汇总 ----------
    print()
    print("+" + "-" * 62 + "+")
    print(f"  自检结果：通过 {passed} 项，失败 {failed} 项，注意 {warned} 项")
    print("+" + "-" * 62 + "+")

    if failed == 0:
        print()
        print("  >>> 一切正常，可以双击 start.bat 启动系统了 <<<")
    else:
        print()
        print("  >>> 有项目未通过，请按上面的提示处理后再试 <<<")

    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
