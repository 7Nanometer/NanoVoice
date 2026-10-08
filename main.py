"""
主程序 —— 网页界面 + 整个流程的调度。

整个系统的流程是这样的：
    输入网址/文章  →  抓取正文  →  AI 生成口播文案  →  切句配音  →  拼成一个音频文件

这个文件负责：
    1. 画出网页界面（用的是 gradio 这个界面库）
    2. 把用户的操作转交给下面三个模块去干活
    3. 管理"语音引擎"（GPT-SoVITS）这个后台服务的启动和停止
"""

import os
import re
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import gradio as gr

import config
from article_extractor import extract_text_from_url
from logger import get_logger, setup_logging
from script_generator import generate_script, refine_script
from speech_synthesizer import (
    check_gpt_sovits_status,
    check_gpt_sovits_status_detailed,
    generate_audio_from_script,
    get_available_voices,
    prepare_refer_audio,
)

logger = get_logger(__name__)

# 多篇文章之间的分隔标记。
# 改造前用的是 "---"，但网页正文里经常出现 ---（比如 markdown 的分隔线），
# 结果一篇文章会被误当成多篇。现在换成这个不太可能自然出现的整行标记。
ARTICLE_SEP = "\n\n======== 文章分隔 ========\n\n"

# 匹配上面那个分隔标记（行首行尾都必须对上，正文里偶然出现几个等号也不会误切）
_SEP_PATTERN = re.compile(r"^\s*={4,}\s*文章分隔\s*={4,}\s*$", re.MULTILINE)

# 保存后台启动的语音服务进程（用于之后停止它）
gpt_sovits_process = None


# ============================================================
# 一、小工具
# ============================================================

def _fmt_signed_percent(value) -> str:
    """
    把界面上滑杆的数字，转成 edge-tts 能接受的格式。

    这是改造前最要命的一个 bug：
        原代码写的是 f"{rate}%" —— 滑杆默认值是 0，拼出来就是 "0%"，
        而 edge-tts 规定必须带正负号（+0% 或 -0%），所以一提交就报
        "Invalid rate '0%'"，11 个声音里 10 个必然失败。

    这个函数统一处理三件事：
        1. 永远带正负号：0 → "+0%"
        2. 转成整数：5.0 → "+5%"（滑杆有时会返回小数）
        3. 补上百分号
    """
    return f"{int(round(float(value))):+d}%"


def new_output_path(prefix: str = "配音", ext: str = "mp3") -> Path:
    """
    生成一个不会重复的输出文件名。

    改造前固定叫 final_audio.mp3，点两次就把上一次的覆盖掉了。
    现在加上"年月日_时分秒 + 4位随机码"，每次都是新文件。
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return config.AUDIO_DIR / f"{prefix}_{stamp}_{uuid.uuid4().hex[:4]}.{ext}"


def split_articles(text: str) -> list:
    """把界面上的长文本按分隔标记切成多篇文章。"""
    parts = _SEP_PATTERN.split(text or "")
    return [p.strip() for p in parts if p.strip()]


def is_port_in_use(port: int) -> bool:
    """测试某个端口是不是已经被占用（被占了说明服务已经在跑）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError as e:
        logger.warning("探测端口 %s 出错: %s", port, e)
        return False


def kill_process_on_port(port: int) -> bool:
    """
    强制结束占用指定端口的进程。

    注意：这是"强杀"，对方程序来不及保存数据。
    所以只在用户明确点击"停止服务"或启动前发现端口被占时使用。
    """
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("执行 netstat 失败: %s", e)
        return False

    pids = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        # netstat 的输出格式是： 协议  本地地址  外部地址  状态  PID
        # 例如：  TCP    127.0.0.1:9880    0.0.0.0:0    LISTENING    12345
        # 改造前的写法是 if f':{port}' in line，会把 98801 这类端口也误判成 9880，
        # 进而杀掉无辜的进程。现在改成精确比对"本地地址"这一列。
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            pids.add(parts[4])

    if not pids:
        return False

    killed_any = False
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", pid],
                capture_output=True,
                timeout=10,
            )
            logger.info("已结束占用端口 %s 的进程 PID=%s", port, pid)
            killed_any = True
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("结束进程 PID=%s 失败: %s", pid, e)

    return killed_any


# ============================================================
# 二、语音引擎（GPT-SoVITS）的后台服务管理
# ============================================================

def start_gpt_sovits_service():
    """
    启动语音引擎服务。用"边跑边报进度"的写法，界面上能看到实时状态。

    这里返回的是生成器（用 yield 而不是 return）——gradio 支持这种写法，
    会把每次 yield 的内容显示到界面上，用户就能看到"正在启动…加载模型…就绪"。
    """
    global gpt_sovits_process

    if check_gpt_sovits_status():
        yield "GPT-SoVITS 服务: ✅ 已经在运行了"
        return

    if gpt_sovits_process is not None and gpt_sovits_process.poll() is None:
        yield "服务正在启动中，请稍候…"
        return

    # —— 启动前检查文件是否齐全 ——
    missing = []
    if not config.GPT_SOVITS_PYTHON.exists():
        missing.append(f"运行环境 {config.GPT_SOVITS_PYTHON}")
    if not config.GPT_SOVITS_API.exists():
        missing.append(f"服务脚本 {config.GPT_SOVITS_API}")
    if not config.SOVITS_WEIGHT.exists():
        missing.append(f"声学模型 {config.SOVITS_WEIGHT}")
    if not config.GPT_WEIGHT.exists():
        missing.append(f"语气模型 {config.GPT_WEIGHT}")
    if missing:
        yield ("❌ 缺少文件，无法启动：\n"
               + "\n".join(f"  · {m}" for m in missing)
               + "\n\n请运行「环境自检」查看详情：python check_env.py")
        return

    # —— 端口被占就先清掉（多半是上一次没关干净的服务）——
    if is_port_in_use(config.SOVITS_PORT):
        yield f"端口 {config.SOVITS_PORT} 被占用，正在清理旧进程…"
        kill_process_on_port(config.SOVITS_PORT)
        time.sleep(2)
        if is_port_in_use(config.SOVITS_PORT):
            yield (f"❌ 端口 {config.SOVITS_PORT} 仍被占用。\n"
                   f"请执行 netstat -ano | findstr :{config.SOVITS_PORT} 查出 PID，"
                   f"再执行 taskkill /F /PID <PID> 结束它。")
            return

    args = [
        str(config.GPT_SOVITS_PYTHON),
        str(config.GPT_SOVITS_API),
        "-d", "cuda",                        # 用显卡推理（没有显卡改成 cpu，但会慢很多）
        "-a", "127.0.0.1",                   # 只允许本机访问，不对局域网开放
        "-p", str(config.SOVITS_PORT),
        "-s", str(config.SOVITS_WEIGHT),     # ★ 你训练的声学模型（原代码指向的是底模）
        "-g", str(config.GPT_WEIGHT),        # ★ 你训练的语气模型
        "-dr", str(config.REF_AUDIO),        # 参考音频
        "-dt", config.REF_TEXT,              # 参考音频对应的文字（原代码填的是占位符）
        "-dl", config.REF_LANG,
    ]
    logger.info("启动语音服务命令: %s", " ".join(args))

    def run_service():
        """在后台线程里跑服务进程，这样不会卡住界面。"""
        global gpt_sovits_process
        try:
            gpt_sovits_process = subprocess.Popen(
                args,
                cwd=str(config.GPT_SOVITS_DIR),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except OSError:
            # 改造前这里没有异常处理：启动失败只在控制台报错，
            # 界面上却显示"启动命令已发送"，用户一直干等。
            logger.exception("启动语音服务进程失败")
            gpt_sovits_process = None

    threading.Thread(target=run_service, daemon=True).start()

    # —— 等待服务真正就绪（加载模型通常要 10-30 秒）——
    yield ("🚀 启动命令已发送，正在等待模型加载…（首次启动约 10-30 秒）\n"
           "会弹出一个黑色命令行窗口显示服务日志，那是正常的。")
    for waited in range(0, 180, 3):
        time.sleep(3)
        if gpt_sovits_process is None:
            yield "❌ 启动失败：进程没能起来。请运行 python check_env.py 检查环境。"
            return
        if gpt_sovits_process.poll() is not None:
            yield ("❌ 服务进程启动后立刻退出了。\n"
                   "常见原因：显卡显存不足、模型文件损坏。\n"
                   "请查看那个黑色命令行窗口里的报错信息。")
            return
        if check_gpt_sovits_status():
            yield (f"✅ 语音服务已就绪（用时约 {waited + 3} 秒）\n"
                   f"当前模型：{config.SOVITS_WEIGHT.stem} + {config.GPT_WEIGHT.stem}")
            _warm_up_sovits()
            return

    yield "⚠️ 等待超时（超过 180 秒）。服务可能仍在加载，可点击「🔄 检查状态」再确认。"


def _warm_up_sovits() -> None:
    """
    预热：先合成一句废话，把"第一次推理特别慢"的开销提前消化掉。

    这样用户真正点"合成配音"时就不会有十几秒的莫名等待。
    """
    try:
        import asyncio

        from speech_synthesizer import text_to_speech_gpt_sovits

        logger.info("开始预热语音模型…")
        warm_file = config.TEMP_DIR / "warmup.wav"
        asyncio.run(text_to_speech_gpt_sovits("预热", str(warm_file), cfg=config))
        if warm_file.exists():
            warm_file.unlink()
        logger.info("预热完成")
    except Exception:
        # 预热失败不影响主流程，只是少了个优化
        logger.warning("预热失败（不影响正常使用）", exc_info=True)


def stop_gpt_sovits_service():
    """停止语音引擎服务。"""
    global gpt_sovits_process

    try:
        if gpt_sovits_process is not None and gpt_sovits_process.poll() is None:
            gpt_sovits_process.terminate()
            time.sleep(1)
    except OSError as e:
        logger.warning("结束服务进程时出错: %s", e)
    finally:
        gpt_sovits_process = None

    # 兜底：如果端口还被占着，说明还有残留进程
    if is_port_in_use(config.SOVITS_PORT):
        kill_process_on_port(config.SOVITS_PORT)
        time.sleep(1)

    if not check_gpt_sovits_status():
        return "⏹️ 语音服务已停止"
    return "⚠️ 服务可能仍在运行，请点击「🔄 检查状态」确认"


def check_sovits_status():
    """检查语音服务状态，返回给界面显示的一段话。"""
    ok, message = check_gpt_sovits_status_detailed()
    if ok:
        return (f"✅ 语音服务已连接\n{message}\n"
                f"当前模型：{config.SOVITS_WEIGHT.stem}")
    return (f"❌ 语音服务未连接\n{message}\n"
            f"提示：点「🚀 启动服务」即可启动")


# ============================================================
# 三、界面上的按钮，具体干什么活
# ============================================================

def extract_from_urls(urls_text):
    """
    从网址列表抓取文章正文。

    改造前的问题：抓取失败时会把 "提取失败: xxx" 这段文字当成文章内容塞进去，
    用户如果没注意就点"生成文案"，这段英文报错会被当成文章喂给大模型。
    现在改成：成功的内容进文章框，失败的单独显示在状态栏。
    """
    urls = [u.strip() for u in (urls_text or "").splitlines() if u.strip()]
    if not urls:
        return "", "请先输入至少一个网址（每行一个）"

    good, bad = [], []
    for i, url in enumerate(urls, 1):
        if not url.startswith(("http://", "https://")):
            bad.append(f"第{i}条「{url}」不是有效网址（必须以 http:// 或 https:// 开头）")
            continue

        ok, content = extract_text_from_url(url, config)
        if ok:
            good.append(content)
        else:
            bad.append(f"第{i}条「{url}」抓取失败：{content}")

    if not good:
        return "", "❌ 全部抓取失败：\n" + "\n".join(bad)

    status = f"✅ 成功抓取 {len(good)} 篇"
    if bad:
        status += f"，失败 {len(bad)} 条：\n" + "\n".join(bad)
    return ARTICLE_SEP.join(good), status


def generate_video_script(articles_text, style, word_count, creativity):
    """点「✨ 生成文案」时调用。"""
    articles = split_articles(articles_text)
    if not articles:
        return "请先输入文章内容，或用网址抓取"
    return generate_script(articles, style, word_count, creativity, config)


def refine_video_script(script, suggestions):
    """点「✏️ 按建议润色」时调用。"""
    if not script or not script.strip():
        return "请先生成文案"
    return refine_script(script, suggestions, config)


def count_words(text):
    """统计字数（不含空格和换行）。界面上每次输入都会调用它。"""
    if not text:
        return "0 字"
    return f"{len(text.replace(' ', '').replace(chr(10), ''))} 字"


async def synthesize_audio(script, voice_name, rate, volume, pause_duration,
                           use_gpt_sovits, refer_audio, prompt_text):
    """
    点「🎤 合成配音」时调用。
    返回三样东西：音频文件、状态文字、可下载文件。
    """
    if not script or not script.strip():
        return None, "请先生成文案", None

    # —— 找出用户选的是哪个声音 ——
    voices = get_available_voices()
    matched = next((v for v in voices if v["name"] == voice_name), None)
    if matched is None:
        # 改造前这里会偷偷换成"云希"，界面上看不出来用户选的那个其实没生效
        logger.warning("界面上传来了未知音色: %s", voice_name)
        return None, f"未找到音色「{voice_name}」，请重新选择", None
    voice = matched["voice"]

    # —— 把语速/音量转成合法格式（这是那个致命 bug 的修复点）——
    rate_str = _fmt_signed_percent(rate)
    volume_str = _fmt_signed_percent(volume)

    output_file = new_output_path()
    use_clone = (voice == "gpt_sovits") or bool(use_gpt_sovits)

    try:
        if use_clone:
            if not check_gpt_sovits_status():
                return None, "语音服务未启动。请先点「🚀 启动服务」并等待就绪。", None

            # 用户上传了参考音频 → 用他自己的声音；没上传 → 用内置的（训练时那段）
            if refer_audio:
                final_refer, err = prepare_refer_audio(refer_audio, config)
                if err:
                    return None, err, None
                ref_text = (prompt_text or "").strip()
                if not ref_text:
                    return None, ("用了自己上传的参考音频，就必须填写「参考音频对应的文本」——"
                                  "也就是那段音频里你实际说的内容，必须一字不差。"), None
            else:
                final_refer = config.REF_AUDIO
                ref_text = config.REF_TEXT
                logger.info("未上传参考音频，使用内置参考音频：%s", final_refer)

            if not Path(final_refer).exists():
                return None, f"参考音频不存在：{final_refer}", None

            audio_path, message = await generate_audio_from_script(
                script=script,
                output_file=str(output_file),
                voice=voice,
                rate=rate_str,
                volume=volume_str,
                pause_duration=pause_duration,
                use_gpt_sovits=True,
                refer_wav_path=str(final_refer),
                prompt_text=ref_text,
                cfg=config,
            )
        else:
            audio_path, message = await generate_audio_from_script(
                script=script,
                output_file=str(output_file),
                voice=voice,
                rate=rate_str,
                volume=volume_str,
                pause_duration=pause_duration,
                use_gpt_sovits=False,
                cfg=config,
            )

        if audio_path is None:
            return None, message or "配音失败", None

        return audio_path, message or "✅ 配音完成", audio_path

    except Exception as e:
        logger.exception("配音过程中出现未预期的错误")
        return None, f"配音出错：{e}\n详情见日志 outputs/logs/app.log", None


# ============================================================
# 四、网页界面
# ============================================================

setup_logging()

with gr.Blocks(title="视频文案自动生成与配音系统") as demo:
    gr.Markdown("# 🎬 多源文本融合与语音克隆配音系统")
    gr.Markdown("输入多篇文章，自动生成视频文案，并使用 AI 语音合成配音")

    # ---------------- 标签页一：文案生成 ----------------
    with gr.Tab("① 文案生成"):
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 输入文章")

                url_input = gr.Textbox(
                    label="网址列表（每行一个）",
                    placeholder="https://example.com/article1\nhttps://example.com/article2",
                    lines=5,
                )
                url_button = gr.Button("🔗 从网址提取文章", variant="primary")

                articles_input = gr.Textbox(
                    label="文章内容（多篇之间用「======== 文章分隔 ========」隔开）",
                    placeholder="文章1内容…\n\n======== 文章分隔 ========\n\n文章2内容…",
                    lines=10,
                )

                with gr.Row():
                    style_dropdown = gr.Dropdown(
                        ["通用", "科技", "生活", "娱乐", "财经"],
                        label="文案风格", value="通用",
                    )
                    word_count_slider = gr.Slider(
                        minimum=300, maximum=5000, step=100,
                        label="目标字数", value=800,
                    )

                creativity_slider = gr.Slider(
                    minimum=0.1, maximum=1.0, step=0.1,
                    label="创意程度（越大越活泼）", value=0.7,
                )

                with gr.Row():
                    generate_button = gr.Button("✨ 生成文案", variant="primary", size="lg")
                    regenerate_button = gr.Button("🔄 重新生成")

                url_status = gr.Textbox(label="抓取状态", interactive=False, lines=3)

            with gr.Column(scale=1):
                gr.Markdown("### 生成的文案")

                script_output = gr.Textbox(
                    label="视频文案（可以直接在这里修改）",
                    placeholder="生成的文案将显示在这里…",
                    lines=15, interactive=True,
                )

                word_count_display = gr.Textbox(
                    label="字数统计", interactive=False, value="0 字",
                )

                refine_suggestions = gr.Textbox(
                    label="修改建议",
                    placeholder="例如：更口语化一些 / 开头再抓人一点",
                    lines=3,
                )
                refine_button = gr.Button("✏️ 按建议润色")

    # ---------------- 标签页二：语音合成 ----------------
    with gr.Tab("② 语音合成"):
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 配音设置")

                voice_dropdown = gr.Dropdown(
                    choices=[v["name"] for v in get_available_voices()],
                    label="选择声音",
                    value=get_available_voices()[0]["name"],
                )

                with gr.Row():
                    rate_slider = gr.Slider(
                        minimum=-50, maximum=50, step=5, label="语速 (%)", value=0,
                    )
                    volume_slider = gr.Slider(
                        minimum=-20, maximum=20, step=1, label="音量 (%)", value=0,
                    )

                pause_slider = gr.Slider(
                    minimum=200, maximum=1500, step=100, label="句间停顿 (毫秒)", value=500,
                )

                gr.Markdown("---")
                gr.Markdown("### 🔊 语音克隆（GPT-SoVITS）")

                # 改造前这里会在界面启动时同步发一次网络请求，把界面卡住 5 秒。
                # 现在只用本地端口探测，瞬间完成。
                sovits_status = gr.Textbox(
                    label="服务状态",
                    value=check_sovits_status(),
                    interactive=False,
                    lines=3,
                )

                with gr.Row():
                    start_sovits_button = gr.Button("🚀 启动服务", variant="primary")
                    stop_sovits_button = gr.Button("⏹️ 停止服务")
                    check_status_button = gr.Button("🔄 检查状态")

                use_gpt_sovits = gr.Checkbox(label="使用语音克隆", value=False)

                gr.Markdown(
                    "*不传参考音频也能用（会用你训练时那段内置音频）。"
                    "想克隆自己的声音，就上传一段 3~10 秒的录音，并填写录音里说的话。*"
                )

                refer_audio_input = gr.Audio(
                    label="参考音频（自己的声音样本，可选）", type="filepath",
                )
                prompt_text_input = gr.Textbox(
                    label="参考音频对应的文本（上传了音频才需要填）",
                    placeholder="请一字不差地写出音频里说的话",
                    lines=3,
                )

                synthesize_button = gr.Button("🎤 合成配音", variant="primary", size="lg")

            with gr.Column(scale=1):
                gr.Markdown("### 音频预览")
                audio_output = gr.Audio(label="合成的音频", type="filepath")
                status_output = gr.Textbox(label="状态", interactive=False, lines=4)
                download_file = gr.File(label="下载音频文件", visible=True)

                gr.Markdown(
                    f"<small>当前克隆模型：{config.SOVITS_WEIGHT.stem} + {config.GPT_WEIGHT.stem}<br>"
                    f"输出目录：{config.AUDIO_DIR}</small>"
                )

    # ---------------- 按钮绑定 ----------------
    url_button.click(extract_from_urls, inputs=url_input, outputs=[articles_input, url_status])

    generate_button.click(
        generate_video_script,
        inputs=[articles_input, style_dropdown, word_count_slider, creativity_slider],
        outputs=script_output,
    )
    regenerate_button.click(
        generate_video_script,
        inputs=[articles_input, style_dropdown, word_count_slider, creativity_slider],
        outputs=script_output,
    )

    script_output.change(count_words, inputs=script_output, outputs=word_count_display)

    refine_button.click(
        refine_video_script,
        inputs=[script_output, refine_suggestions],
        outputs=script_output,
    )

    start_sovits_button.click(start_gpt_sovits_service, outputs=sovits_status)
    stop_sovits_button.click(stop_gpt_sovits_service, outputs=sovits_status)
    check_status_button.click(check_sovits_status, outputs=sovits_status)

    synthesize_button.click(
        synthesize_audio,
        inputs=[script_output, voice_dropdown, rate_slider, volume_slider, pause_slider,
                use_gpt_sovits, refer_audio_input, prompt_text_input],
        outputs=[audio_output, status_output, download_file],
    )


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("启动系统 | 语音引擎: %s", config.GPT_SOVITS_DIR)
    logger.info("克隆模型: %s + %s", config.SOVITS_WEIGHT.name, config.GPT_WEIGHT.name)
    logger.info("输出目录: %s", config.AUDIO_DIR)

    # 只有在需要对外提供访问时才启用密码（本机访问不用密码，方便）
    auth = None
    if config.APP_HOST != "127.0.0.1" and config.APP_USER:
        auth = (config.APP_USER, config.APP_PASS)
        logger.info("已启用访问密码，用户名: %s", config.APP_USER)
    elif config.APP_HOST != "127.0.0.1":
        logger.warning("⚠️ 已对局域网开放且未设密码，请注意安全！")

    demo.launch(
        server_name=config.APP_HOST,
        server_port=config.APP_PORT,
        # 只放行输出目录里的文件可下载。
        # 改造前写的是 allowed_paths=["."]，等于把整个项目文件夹对外开放，
        # 别人用 http://你的IP:7860/file=.env 就能直接下载到 API 密钥。
        allowed_paths=[str(config.OUTPUT_DIR)],
        # 黑名单兜底：万一白名单配错，这个文件也绝不允许下载
        blocked_paths=[str(config.ROOT / ".env")],
        auth=auth,
        share=False,
        inbrowser=True,
        show_error=True,
        prevent_thread_lock=True,
    )

    logger.info("界面已启动: http://%s:%s", config.APP_HOST, config.APP_PORT)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到退出信号，正在关闭…")
        if gpt_sovits_process is not None and gpt_sovits_process.poll() is None:
            logger.info("正在关闭语音服务…")
            gpt_sovits_process.terminate()
