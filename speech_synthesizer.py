"""
语音合成模块 —— 把文字变成声音。

提供两条完全不同的路：
    A. edge-tts（微软的在线语音）
       · 免费、有 10 个现成音色（云希、晓晓…）
       · 需要联网
       · 音色是固定的，不能克隆某个人的声音

    B. GPT-SoVITS（本地语音克隆）
       · 运行在你自己电脑上，完全不联网
       · 能克隆指定声音（你训练的 7Nanometer 模型）
       · 需要显卡，速度取决于电脑

两条路都是"逐句合成、再拼成整段"，所以共同的步骤是：
    切句  →  逐句生成音频  →  （响度拉平）  →  句间插停顿  →  拼成一个文件  →  导出 mp3
"""

import asyncio
import math
import re
import time
from pathlib import Path

import edge_tts
import requests
from pydub import AudioSegment

import config
from logger import get_logger

logger = get_logger(__name__)

# 网络会话：复用连接，比每次新建快。
# trust_env = False 表示"不看系统的代理设置"——本机访问 127.0.0.1 时如果绕代理会连不上。
session = requests.Session()
session.trust_env = False


def _setup_ffmpeg(cfg) -> None:
    """
    告诉 pydub 用哪个 ffmpeg。

    ffmpeg 是处理音频的工具，pydub 自己不会解码 mp3，全靠它。
    改造前这里没设置，依赖系统里恰好装了 ffmpeg 并加进了 PATH——
    换台电脑就报 "ffmpeg not found"。现在优先用语音引擎自带的那个。
    """
    ffmpeg_path = Path(cfg.FFMPEG)
    AudioSegment.converter = str(ffmpeg_path)
    # ffprobe 是 ffmpeg 的兄弟工具，用来读取音频信息（pydub 用得着）
    ffprobe = ffmpeg_path.with_name("ffprobe.exe")
    if ffprobe.exists():
        AudioSegment.ffprobe = str(ffprobe)


# ============================================================
# 一、文字预处理
# ============================================================

def sanitize_for_tts(text: str) -> str:
    """
    清掉不该被"念出来"的东西。

    改造前的实际问题：给大模型的提示词里要求文案带【开头：抓住眼球】这类结构标记，
    而模型真的把它们写进了正文，配音时就被一字不差地念出来：
    "【开头：抓住眼球】"——听起来很怪。
    现在配音前统一清掉这些标记、序号和 markdown 符号。
    """
    if not text:
        return ""
    # 清掉【】包裹的标记（包括中文书名号里的）
    text = re.sub(r"【[^】]*】", "", text)
    # 清掉行内的 Markdown 符号：**加粗**、__下划线__、`代码`
    # （不清的话这些星号可能被语音引擎当字符念出来："星号星号注意"）
    text = re.sub(r"\*\*|__|`+", "", text)
    # Markdown 链接 [文字](网址) → 只保留"文字"，丢掉网址部分
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # 清掉每行开头的 Markdown 记号（#、*、>、-）和序号（1. 2、 三、）
    text = re.sub(r"^[\s>*#\-]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.、)]\s*", "", text, flags=re.MULTILINE)
    # 多余的空行压成一个
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text_into_chunks(text: str, max_length: int = 300) -> list:
    """
    把长文案切成小块，逐块送去合成。

    为什么要切：一次性丢几千字给语音引擎，容易吃爆显存或者超时；
    切成小块还能自然地在句号处停顿，听感更像真人说话。

    切法分两步：
        1. 先按句号、问号、叹号、分号、换行切成"句子"
        2. 再把句子攒成不超过 max_length 的"块"

    改造前漏了第三步：如果某一"句"本身就超长（比如一整段没有标点的英文），
    它会被原封不动塞进去，导致单块超限。现在加了硬切兜底。
    """
    sentences = []
    current = ""
    for i, char in enumerate(text):
        current += char
        if len(current) <= 10:
            continue   # 太短的句子不切，避免"你好。"这种被单独切出来，听着不连贯
        # 中文/通用句末标点（2026-10-09 补上省略号"…"）
        if char in "。！？；…\n":
            sentences.append(current.strip())
            current = ""
        # 英文叹号问号：直接用（中文文案里罕见，但英文文案需要）
        elif char in "!?":
            sentences.append(current.strip())
            current = ""
        # 英文句号：只有"点 + 空格/行尾"才算句末——
        # 这样 3.14、v2.0、example.com 这类小数点不会被误切。
        # （句尾补一个空格：英文断句后，原句号后面的空格会落在下一句的开头
        #  被 strip 掉，导致攒块合并时单词粘连"stays.Next"）
        elif char == "." and (i + 1 >= len(text) or text[i + 1] in " \n\t"):
            sentences.append(current.strip() + " ")
            current = ""
    if current.strip():
        sentences.append(current.strip())

    # 超长句尽量在标点处切开（2026-10-09 改进）。
    # 原来直接按字数均分，会把词拦腰切断——实测真实文案有一块结尾恰好切在
    # 词中间，后半句被引擎自动补了句号，听感"说到一半戛然而止"。
    safe_sentences = []
    for s in sentences:
        while len(s) > max_length:
            # 在"目标位置往前 15 字"的窗口里，找最靠后的一个标点。
            # 右边界用 max_length+1：允许标点正好在 max_length 这个位置
            # （否则恰好在临界处的逗号会被漏掉）
            window_start = max(0, max_length - 15)
            cut = -1
            for p in "，、,;：:）)":
                pos = s.rfind(p, window_start, max_length + 1)
                if pos > cut:
                    cut = pos
            if cut == -1:
                cut = max_length - 1     # 窗口里实在没标点，才硬切
            safe_sentences.append(s[:cut + 1])
            s = s[cut + 1:]
        if s:
            safe_sentences.append(s)

    # 攒块
    chunks = []
    current_chunk = ""
    for s in safe_sentences:
        if len(current_chunk) + len(s) <= max_length:
            current_chunk += s
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = s
    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# ============================================================
# 二、两种语音引擎
# ============================================================

async def text_to_speech_edge(text, output_file, voice, rate, volume):
    """用微软在线语音合成一句，存成 mp3。"""
    # 万一网络抖动，重试两次——答辩现场最怕这种偶发失败
    last_error = None
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
            await communicate.save(output_file)
            return output_file
        except Exception as e:
            last_error = e
            if attempt < 2:
                logger.warning("微软语音第 %s 次失败（%s），1 秒后重试", attempt + 1, e)
                await asyncio.sleep(1)
    raise last_error


async def text_to_speech_gpt_sovits(text, output_file, refer_wav_path="",
                                    prompt_text="", prompt_language="zh",
                                    cfg=None, speed=1.0):
    """
    用本地 GPT-SoVITS 合成一句，存成 wav。

    原理：把文字发给本机 9880 端口上的服务，服务用显卡算完把音频流发回来。
    返回：成功时是文件路径字符串；失败时是 (None, 错误说明) 这种两元组。
    """
    api_url = cfg.SOVITS_API_URL

    payload = {
        "text": text,
        # 2026-10-09 由 "all_zh" 改为 "zh"：实测内容完整度 94% → 100%
        # （"all_zh" 会把中英混排按中文规则处理，遇到英文/数字容易吞字）
        "text_language": "zh",
        "top_k": 30,          # 采样参数，控制"随机性"
        "top_p": 0.8,
        "temperature": 0.8,
        "speed": speed,       # 由界面上的"语速"滑块换算而来（1.0 = 原速）
        "sample_steps": 32,   # 推理步数，越大越精细也越慢
        # 2026-10-09 实测结论：这里**必须保持为空**（不要开！）。
        # 原因：我们的 50 字切块已经管好了停顿；再让服务端按标点"二次切分"，
        # 会切出大量内部接缝——实测吞字显著上升（相似度 0.97→0.92，
        # 出现 5 处真实吞字），关掉后 0 吞字、完全恢复。
        "cut_punc": "",
    }

    # 带上参考音频和它对应的文字（决定"学谁的声音"）
    if refer_wav_path and prompt_text:
        payload["refer_wav_path"] = refer_wav_path
        payload["prompt_text"] = prompt_text
        payload["prompt_language"] = prompt_language

    logger.debug("请求 GPT-SoVITS：%s 字，参考音频=%s", len(text), refer_wav_path or "（用服务默认）")

    for attempt in range(3):
        try:
            response = session.post(api_url + "/", json=payload, timeout=180)

            if response.status_code == 200:
                # 服务正常时返回的是音频二进制数据，直接写文件
                content_type = response.headers.get("content-type", "")
                if "audio" not in content_type and "octet-stream" not in content_type:
                    # 状态码是 200 但内容不是音频 —— 说明服务端其实出错了。
                    # 改造前会把这种内容当音频写进文件，等到后面解码时才报莫名其妙的错。
                    snippet = response.text[:200]
                    logger.error("服务返回了非音频内容（%s）：%s", content_type, snippet)
                    return None, f"语音服务返回了异常内容（不是音频）：{snippet}"

                with open(output_file, "wb") as f:
                    f.write(response.content)
                return output_file

            # 状态码不是 200，说明推理失败（比如文本太长、模型报错）
            try:
                info = response.json()
                error_msg = info.get("message") or info.get("detail") or str(info)
            except ValueError:
                error_msg = response.text[:300]
            logger.error("GPT-SoVITS 返回错误 %s：%s", response.status_code, error_msg)

            # 服务端明确报错时重试没意义（同样的输入还会同样失败），直接返回
            return None, f"语音合成失败（HTTP {response.status_code}）：{error_msg}"

        except requests.exceptions.ConnectionError as e:
            logger.warning("连接语音服务失败（第 %s 次）: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2)
                continue
            return None, ("连不上语音服务。请确认已经点过「🚀 启动服务」并且状态显示已连接。")

        except requests.exceptions.Timeout as e:
            logger.warning("语音服务响应超时（第 %s 次）: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2)   # 改造前这里没等待，等于连打三次
                continue
            return None, "语音服务响应超时。文本可能过长，或显卡正忙，请稍后重试。"

        except Exception as e:
            logger.exception("调用语音服务时发生意外错误")
            if attempt < 2:
                time.sleep(2)
                continue
            return None, f"语音合成出错：{e}"


# ============================================================
# 三、参考音频处理
# ============================================================

def _ffprobe_duration(path) -> float:
    """读取音频的时长（秒）。读不出来就返回 0。"""
    try:
        import subprocess

        result = subprocess.run(
            [str(AudioSegment.ffprobe), "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        logger.debug("读取音频时长失败", exc_info=True)
        return 0.0


def prepare_refer_audio(uploaded_path, cfg):
    """
    处理用户上传的参考音频，让它符合语音克隆的要求。

    做两件事：
        1. 统一转成 32kHz 单声道 wav —— 和训练模型时的格式一致，
           否则引擎要现场转换，音质和速度都会受影响
        2. 检查时长：太短学不到音色（<3 秒），太长会拖慢且效果反而下降（>15 秒）

    为什么要拷一份到项目目录：界面上传的文件存在临时目录里，随时可能被清理；
    直接拿着那个路径去用，过一会儿就可能"文件不存在"。

    返回：(处理好的文件路径, 错误说明)。成功时错误说明是空字符串。
    """
    if not uploaded_path:
        return None, "未提供参考音频"

    src = Path(uploaded_path)
    if not src.exists():
        return None, f"参考音频文件不存在：{src}"

    _setup_ffmpeg(cfg)

    from datetime import datetime
    dst = cfg.REF_DIR / f"refer_{datetime.now():%Y%m%d_%H%M%S}.wav"

    try:
        audio = AudioSegment.from_file(str(src))
        audio = audio.set_frame_rate(32000).set_channels(1).set_sample_width(2)
        audio.export(str(dst), format="wav")
    except Exception as e:
        logger.exception("参考音频转码失败")
        return None, f"参考音频处理失败（{e}）。请换一个常见的格式重新上传（wav / mp3 / m4a）。"

    duration = _ffprobe_duration(dst) or (len(audio) / 1000.0)

    if duration < 3:
        dst.unlink(missing_ok=True)
        return None, (f"参考音频只有 {duration:.1f} 秒，太短了，学不出音色。"
                      f"建议用 5~10 秒的清晰人声。")
    if duration > 15:
        logger.warning("参考音频 %s 秒偏长，克隆效果可能下降（建议 5~10 秒）", round(duration, 1))

    logger.info("参考音频已就绪：%s（%.1f 秒）", dst.name, duration)
    return dst, ""


# ============================================================
# 四、响度对齐
# ============================================================

def _match_loudness(segment, target_dbfs, max_adjust_db=6.0):
    """
    把一句话的音量往统一水平"轻轻推一把"。

    为什么需要：逐句合成时，每句话的音量经常不一样（尤其 GPT-SoVITS 和微软语音
    混着用的时候），拼起来会"忽大忽小"，听着不舒服。

    dBFS 是衡量音量的单位（0 是最大值，负数越小越轻）。
    这个函数算出"当前音量"和"目标音量"差多少，然后统一加减。

    2026-10-09 上限由 12 分贝收紧到 6 分贝：
    原设置会把每句话都"强行拉平"到同一音量，句子之间自然的轻重起伏
    （也就是"情感"）被一起抹掉了。改成只做轻微矫正——
    差得多的句子保持自己的相对大小，只把跑偏的部分拉回来一点。
    另外也避免把很轻的片段（可能是底噪）放大到失真。
    """
    if segment.dBFS == float("-inf"):
        return segment   # 整段是静音，没什么可调的

    diff = target_dbfs - segment.dBFS
    diff = max(-max_adjust_db, min(max_adjust_db, diff))
    return segment.apply_gain(diff)


# ============================================================
# 五、主流程：文案 → 音频
# ============================================================

async def generate_audio_from_script(script, output_file="final_output.mp3",
                                     voice="zh-CN-YunxiNeural", rate="+0%", volume="+0%",
                                     pause_duration=500, use_gpt_sovits=False,
                                     refer_wav_path="", prompt_text="", cfg=None):
    """
    把整段文案变成音频文件。返回 (音频路径, 提示信息)；失败时路径为 None。

    script          —— 文案正文
    output_file     —— 输出 mp3 的路径
    voice/rate/volume —— 音色、语速、音量
    pause_duration  —— 句与句之间插多少毫秒的停顿
    use_gpt_sovits  —— True 用本地克隆，False 用微软在线语音
    refer_wav_path / prompt_text —— 克隆时的参考音频和它对应的文字
    """
    _setup_ffmpeg(cfg)

    script = sanitize_for_tts(script)
    if not script:
        return None, "文案内容是空的（或者清理后没有可朗读的内容）"

    chunk_max = cfg.SOVITS_CHUNK_MAX if use_gpt_sovits else cfg.EDGE_CHUNK_MAX
    chunks = split_text_into_chunks(script, max_length=chunk_max)
    if not chunks:
        return None, "文案切分后没有内容，无法配音"

    # 把界面上的 "+5%" / "-10%" 这类文本解析成数字
    # （微软语音由 edge-tts 接收原文；本地克隆要用数字，见下面两处）
    def _pct(value) -> int:
        try:
            return int(str(value).replace("%", "").replace("+", ""))
        except ValueError:
            return 0

    rate_pct = _pct(rate)
    volume_pct = _pct(volume)

    logger.info("开始配音：共 %s 句，引擎=%s，音色=%s",
                len(chunks), "本地克隆" if use_gpt_sovits else "微软在线", voice)

    if use_gpt_sovits and (not refer_wav_path or not prompt_text):
        return None, "使用语音克隆需要提供参考音频和对应的文本"

    # 每个任务用独立的临时目录，避免两次合成互相覆盖（改造前固定叫 temp_0.wav）
    import tempfile
    job_dir = Path(tempfile.mkdtemp(prefix="tts_", dir=str(cfg.TEMP_DIR)))

    audio_segments = []

    try:
        for i, chunk in enumerate(chunks):
            temp_file = job_dir / f"{i}.{'wav' if use_gpt_sovits else 'mp3'}"

            if use_gpt_sovits:
                result = await text_to_speech_gpt_sovits(
                    chunk, str(temp_file), refer_wav_path, prompt_text,
                    "zh", cfg,
                    # 语速滑块接进本地克隆：+10% → 1.1 倍速
                    # （2026-10-09 之前 speed 写死 1.0，滑块在克隆模式下"静默失效"）
                    speed=1.0 + rate_pct / 100,
                )
                if isinstance(result, tuple):
                    return None, f"第 {i + 1}/{len(chunks)} 句合成失败：{result[1]}"
            else:
                try:
                    await text_to_speech_edge(chunk, str(temp_file), voice, rate, volume)
                except Exception as e:
                    logger.exception("微软语音合成失败")
                    return None, (f"第 {i + 1}/{len(chunks)} 句合成失败：{e}\n"
                                  f"提示：微软语音需要联网。如果网络不通，"
                                  f"可以改用「语音克隆」（本地运行，不需要网络）。")

            if not temp_file.exists():
                return None, f"第 {i + 1} 句合成后没有生成文件"

            try:
                audio = AudioSegment.from_file(str(temp_file))
            except Exception as e:
                logger.exception("读取刚生成的音频失败：%s", temp_file)
                return None, f"第 {i + 1} 句的音频文件无法读取：{e}"

            # 统一格式，避免不同来源的音频采样率/声道数不一致、拼接时出错
            audio = audio.set_frame_rate(32000).set_channels(1).set_sample_width(2)

            if cfg.NORMALIZE_AUDIO:
                audio = _match_loudness(audio, cfg.TARGET_DBFS)

            # 音量滑块：微软语音由 edge-tts 自己应用；本地克隆在这里手动加。
            # 放在响度对齐"之后"，否则刚调好的音量会被对齐步骤抹掉。
            # 百分比换算：+20% ≈ 提升 1.6 分贝（感知上大致等量）
            if use_gpt_sovits and volume_pct:
                audio = audio.apply_gain(20 * math.log10(1 + volume_pct / 100))

            audio_segments.append(audio)

            # 句间插停顿（最后一句后面不用插）
            if i < len(chunks) - 1:
                audio_segments.append(
                    AudioSegment.silent(duration=pause_duration, frame_rate=32000)
                )

            logger.debug("已完成 %s/%s 句（%s 毫秒）", i + 1, len(chunks), len(audio))

        if not audio_segments:
            return None, "没有生成任何音频内容"

        combined = audio_segments[0]
        for seg in audio_segments[1:]:
            combined = combined + seg

        # —— 峰值保护（2026-10-09 新增）——
        # 逐句做响度对齐后直接相加，峰值可能顶到 0 dBFS，导出就是削顶爆音
        # （实测过：成品峰值 0.00 dBFS）。这里检查一下：超过 -1.5 dBFS 就
        # 整体等量降下来——整体降不破坏句子之间的比例，只防爆音。
        # 留 1.5 分贝是想给 mp3 的编码过冲留余量：wav 里限到 -1.5，
        # 转成 mp3 再解码出来大约 -1.2（实测过约 0.2-0.3 分贝的抬升）。
        peak = combined.max_dBFS
        if peak > -1.5:
            combined = combined.apply_gain(-1.5 - peak)
            logger.info("峰值保护生效：%.2f dBFS → -1.5 dBFS", peak)

        combined.export(
            str(output_file),
            format="mp3",
            bitrate=cfg.MP3_BITRATE,
        )

        if not Path(output_file).exists():
            return None, "音频导出失败：输出文件没有生成"

        total_sec = len(combined) / 1000.0
        size_kb = Path(output_file).stat().st_size / 1024
        logger.info("配音完成：%s（%.1f 秒，%.0f KB）", output_file, total_sec, size_kb)

        return str(output_file), f"✅ 配音完成：{len(chunks)} 句，时长约 {total_sec:.0f} 秒"

    except Exception as e:
        logger.exception("音频合成过程中出错")
        return None, f"音频合成失败：{e}"

    finally:
        # 不管成功失败，都把这次任务的临时文件整个删掉
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)


# ============================================================
# 六、音色列表与服务状态
# ============================================================

def get_available_voices():
    """返回所有可选的声音。前 10 个是微软在线音色，最后一个是本地克隆。"""
    return [
        {"name": "云希（男声，推荐）", "voice": "zh-CN-YunxiNeural"},
        {"name": "云扬（男声，新闻腔）", "voice": "zh-CN-YunyangNeural"},
        {"name": "云帆（男声，年轻）", "voice": "zh-CN-YunfanNeural"},
        {"name": "晓晓（女声，推荐）", "voice": "zh-CN-XiaoxiaoNeural"},
        {"name": "晓辰（女声）", "voice": "zh-CN-XiaochenNeural"},
        {"name": "晓莉（女声）", "voice": "zh-CN-XiaoliNeural"},
        {"name": "晓梅（女声）", "voice": "zh-CN-XiaomeiNeural"},
        {"name": "晓楠（女声）", "voice": "zh-CN-XiaonanNeural"},
        {"name": "晓婷（女声）", "voice": "zh-CN-XiaotingNeural"},
        {"name": "晓宇（女声）", "voice": "zh-CN-XiaoyuNeural"},
        {"name": "🎙️ 我的克隆声音（本地，需启动服务）", "voice": "gpt_sovits"},
    ]


def check_gpt_sovits_status() -> bool:
    """检查本地语音服务是否活着。活着返回 True。"""
    ok, _ = check_gpt_sovits_status_detailed()
    return ok


def check_gpt_sovits_status_detailed():
    """
    检查本地语音服务状态，返回 (是否正常, 说明文字)。

    关键点：探活要挑一个"不干活"的接口。
    改造前用的是 GET /?text=test&text_language=all_zh ——
    那个接口会真的跑一次语音合成（白烧显卡，还要好几秒），
    5 秒超时经常来不及，导致服务明明正常却显示"未连接"。

    /docs 是服务自带的接口文档页，纯静态，毫秒级返回，不碰显卡。
    """
    url = config.SOVITS_API_URL

    try:
        response = session.get(url + "/docs", timeout=3)
        if response.status_code == 200:
            return True, "服务正常"
        return False, f"服务返回了意外状态码：{response.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "连接失败（服务没启动，或端口被占用）"
    except requests.exceptions.Timeout:
        return False, "请求超时（服务可能正在忙）"
    except Exception as e:
        return False, f"未知错误：{e}"
