"""
配置总控台 —— 整个项目所有"可以改的设定"都集中在这个文件里。

为什么要单独搞一个文件？
    改造前，G 盘路径、模型文件名、端口号这些东西散落在 4 个代码文件和 1 个 bat 里，
    一共十几处。想换个位置就得满项目找，漏一处就报错。
    现在全部集中到这里，以后要改只改这一个文件。

换电脑部署时，通常只需要改 .env（密钥）——路径会自动跟着项目走。
"""

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv


# ============================================================
# 一、项目根目录
# ============================================================
# __file__ 是"当前这个文件自己的位置"，.parent 就是上一级目录（项目根目录）。
# 这样写的好处：整个项目文件夹搬到哪个盘、改成什么名字，代码都能自己找到自己。
ROOT = Path(__file__).resolve().parent

# 读取 .env 里的密钥。必须指定 utf-8，否则中文和特殊符号会乱码。
load_dotenv(ROOT / ".env", encoding="utf-8")


# ============================================================
# 二、关于网络代理（这里有个坑，特意说明）
# ============================================================
# 这台电脑上网可能配了代理（比如 127.0.0.1:7897），访问境外网站要靠它。
#
# 【千万不要在这里设置 no_proxy 环境变量！】
# 听起来"让本机地址不走代理"很合理，但 Python 的代理探测逻辑是：
#     getproxies_environment() or getproxies_registry()
# 也就是说，只要环境变量里存在**任何一个**带 _proxy 结尾的变量，
# 它就直接返回、不再去读 Windows 系统里配置的代理。
# 结果是：为了排除一个本机地址，把整个代理都关掉了，
# 所有境外网站（包括网页抓取）全部连不上。
# （改造前的代码就是这么写的，属于隐藏已久的问题。）
#
# 本机地址不走代理这件事，由 speech_synthesizer.py 里的
#     session.trust_env = False
# 单独处理——那个会话完全不看代理设置，效果更干净。


# ============================================================
# 三、GPT-SoVITS 语音引擎的位置
# ============================================================
def _pick_gpt_sovits_dir() -> Path:
    """
    自动寻找语音引擎在哪。

    优先顺序：
        1. 项目目录里的 GPT-SoVITS 文件夹 —— 搬家后的正常情况
        2. .env 里 GPT_SOVITS_DIR 指定的位置 —— 还没搬家时的过渡方案
        3. 都没有就返回项目内那个路径（让它不存在，好让 check_env.py 报出清楚的错误，
           而不是在这里直接崩溃）
    """
    local = ROOT / "GPT-SoVITS"
    if (local / "api.py").exists():
        return local

    configured = os.getenv("GPT_SOVITS_DIR", "").strip()
    if configured and (Path(configured) / "api.py").exists():
        return Path(configured)

    return local


GPT_SOVITS_DIR = _pick_gpt_sovits_dir()

# 语音引擎自带的便携版 Python（里面装好了 torch 等深度学习库，不用另装）
GPT_SOVITS_PYTHON = GPT_SOVITS_DIR / "runtime" / "python.exe"

# 语音服务的启动脚本
GPT_SOVITS_API = GPT_SOVITS_DIR / "api.py"

# ffmpeg 是处理音频的瑞士军刀（转格式、拼接、调码率都靠它）。
# 语音引擎自带了一个，优先用它 —— 这样换电脑不用额外安装 ffmpeg。
_ffmpeg_bundled = GPT_SOVITS_DIR / "runtime" / "ffmpeg.exe"
if _ffmpeg_bundled.exists():
    FFMPEG = _ffmpeg_bundled
else:
    # 兜底：去系统 PATH 里找
    FFMPEG = Path(shutil.which("ffmpeg") or "ffmpeg")


# ============================================================
# 四、用哪个声音模型
# ============================================================
# 说明：GPT-SoVITS 的语音克隆由两部分模型配合完成 ——
#   SoVITS 负责"把文字变成声学特征"（决定音色像不像）
#   GPT    负责"把声学特征变成声音"（决定语气自然不自然）
# 改造前代码里填的是"底模"（出厂自带、没学过你的声音），
# 你 6 月 16 号训练的 7Nanometer 模型躺在硬盘里没被用过。
# 现在改成默认加载你训练的版本。

def _resolve_path(value: str, default: Path) -> Path:
    """
    把配置里的路径变成"绝对路径"。

    为什么需要这一步：语音引擎工作在自己的目录下，如果给它一个
    "GPT-SoVITS\\SoVITS_weights_v2Pro\\xxx.pth" 这样的相对路径，
    它会以"自己的目录"为起点去找，结果找错地方。
    所以这里统一处理：相对路径一律按"项目根目录"为起点补全。
    """
    if not value:
        return default
    p = Path(value)
    if not p.is_absolute():
        p = ROOT / p
    return p


# 你训练的声学模型（带 _e8_s272 后缀的是最后一轮，效果通常最好）
SOVITS_WEIGHT = _resolve_path(
    os.getenv("SOVITS_WEIGHT", ""),
    GPT_SOVITS_DIR / "SoVITS_weights_v2Pro" / "7Nanometer_e8_s272.pth",
)

# 你训练的语气模型
GPT_WEIGHT = _resolve_path(
    os.getenv("GPT_WEIGHT", ""),
    GPT_SOVITS_DIR / "GPT_weights_v2Pro" / "7Nanometer-e15.ckpt",
)

# 参考音频：启动时用哪段声音作为"音色参考样本"。
# 这是训练素材里切出来的 9.7 秒片段（训练记录推荐 3-10 秒）。
REF_AUDIO = _resolve_path(
    os.getenv("REF_AUDIO", ""),
    GPT_SOVITS_DIR / "logs" / "7Nanometer" / "5-wav32k"
    / "阿基拉.mp3_0000000000_0000310400.wav",
)

# 参考音频对应的文字 —— 必须和音频里真实说的话**一字不差**。
# 改造前这里填的是占位符"测试语音克隆"，和音频内容对不上，会把音色和语调带偏。
# 下面这句话是从训练记录 2-name2text.txt 里抄来的原文。
REF_TEXT = os.getenv(
    "REF_TEXT",
    "大有克阳这个名字大家应该并不陌生.一九八零年,二十六岁的大有克阳,"
    "凭借着童梦这部作品获得了第四届日本大赏.",
)

# 参考音频的语言
REF_LANG = os.getenv("REF_LANG", "zh")


# ============================================================
# 五、语音服务地址
# ============================================================
SOVITS_API_URL = os.getenv("SOVITS_API_URL", "http://127.0.0.1:9880")
SOVITS_PORT = int(os.getenv("SOVITS_PORT", "9880"))


# ============================================================
# 六、音频参数
# ============================================================
# 一段文字太长会导致合成不稳定（可能吃掉大量显存、甚至超时），
# 所以要切成小块逐块合成再拼起来。这两个数字就是"每块最多几个字"。
EDGE_CHUNK_MAX = int(os.getenv("EDGE_CHUNK_MAX", "300"))   # 微软在线音色：可以长一点
SOVITS_CHUNK_MAX = int(os.getenv("SOVITS_CHUNK_MAX", "100"))  # 本地克隆：短一点更稳、停顿更自然

# 响度对齐目标值（单位 dBFS，越接近 0 越响，负数是相对标准音量）。
# 逐句合成时，每句话的音量可能忽大忽小；统一拉平到这个目标值再拼接，听感会平稳很多。
TARGET_DBFS = float(os.getenv("TARGET_DBFS", "-20.0"))
NORMALIZE_AUDIO = os.getenv("NORMALIZE_AUDIO", "1") == "1"

# 导出 mp3 的码率。人声用 96k 足够清晰；原来写 192k 会让文件白白大两三倍。
MP3_BITRATE = os.getenv("MP3_BITRATE", "96k")


# ============================================================
# 七、文案生成（大模型接口）
# ============================================================
# 用的是 DeepSeek 的接口（通过 OpenAI 官方 SDK 调用，因为格式兼容）
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))  # 等大模型回话最多等多久（秒）

# 每篇文章最多取多少字送给大模型（太长会浪费额度且没必要）
ARTICLE_MAX_CHARS = int(os.getenv("ARTICLE_MAX_CHARS", "3000"))
# 抓网页时最多保留多少字
FETCH_MAX_CHARS = int(os.getenv("FETCH_MAX_CHARS", "6000"))


# ============================================================
# 八、输入输出的目录
# ============================================================
OUTPUT_DIR = ROOT / "outputs"
AUDIO_DIR = OUTPUT_DIR / "audio"      # 成品配音
SCRIPT_DIR = OUTPUT_DIR / "scripts"   # 生成的文案
TEMP_DIR = OUTPUT_DIR / "temp"        # 分句的中间文件（用完就删）
LOG_DIR = OUTPUT_DIR / "logs"         # 运行日志
REF_DIR = OUTPUT_DIR / "refer"        # 上传的参考音频副本

# 一次性把需要的目录都建好（exist_ok=True 表示已存在也不报错）
for _d in (AUDIO_DIR, SCRIPT_DIR, TEMP_DIR, LOG_DIR, REF_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ============================================================
# 九、网页界面设置
# ============================================================
# 安全提醒：默认绑 127.0.0.1（只有本机能打开这个界面）。
# 改造前绑的是 0.0.0.0（同一个 WiFi 下所有人都能打开），而且把整个项目文件夹
# 设成了"可下载"，导致别人可以直接下载到 .env 里的 API 密钥。
# 如果你答辩时需要让别的电脑/手机访问，把 APP_HOST 改成 0.0.0.0 并务必设置密码。
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "7860"))
APP_USER = os.getenv("APP_USER", "")
APP_PASS = os.getenv("APP_PASS", "")
