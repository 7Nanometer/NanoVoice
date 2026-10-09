"""
新版网页界面 —— 后端服务（"暗色录音棚"重写版）

【这份文件是干嘛的】
    旧界面是 Gradio（一个 Python 界面库）自动搭的，省事但长相完全被框架决定。
    现在换成"自己写的网页"，视觉上想做成什么样就能做成什么样。

【怎么做到"换界面不动功能"】
    Python 这边干活的逻辑（抓文章、生成文案、配音）一行都没改，
    还是 main.py 里原来那些函数。本文件只做两件事：
      1. 把那些函数包装成一个个"接口"（API），网页前端通过接口呼叫它们；
      2. 把 static/ 文件夹里的网页（HTML/CSS/JS）发给浏览器。

    名词解释——"接口（API）"：
        就像餐厅的菜单。前端是服务员（负责跟客人打交道、端菜摆盘），
        后端是厨房（真正炒菜的）。服务员不用会炒菜，厨房不用管摆盘，
        两边照着菜单上的菜名对接就行。"接口"就是这份菜单。

【启动方式】
    python webui/server.py        （或在项目根目录双击新的 start.bat）
"""

import asyncio
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

# —— 让 import 能找到项目根目录的 config.py 等模块（不管从哪个目录启动都能跑）——
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from file_reader import SUPPORTED_EXTENSIONS, read_one_file
from logger import get_logger
from main import (
    _looks_like_our_app,
    extract_from_urls,
    generate_video_script,
    is_port_in_use,
    refine_video_script,
    start_gpt_sovits_service,
    stop_gpt_sovits_service,
    synthesize_audio,
)
from speech_synthesizer import check_gpt_sovits_status_detailed, get_available_voices

logger = get_logger(__name__)

# ============================================================
# 一、基础设置
# ============================================================

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 用户上传的文件（文章、参考音频）存在这里。
# 每次上传都会生成一个随机前缀，不会互相覆盖。
UPLOAD_DIR = config.TEMP_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 网页服务监听哪个端口。默认跟旧界面一样（在设置里改 APP_PORT 也管用）。
WEB_PORT = int(os.environ.get("WEB_PORT", config.APP_PORT))

# 参考音频允许的格式（语音克隆用）
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".wma"}

app = FastAPI(title="多源文本融合与语音克隆配音系统", docs_url=None, redoc_url=None)

# 把三个文件夹“挂”到网址上：
#   /static/... → 网页自己的文件（HTML/CSS/JS/字体）
#   /audio/...  → 合成出来的音频（只能下载这个文件夹里的东西）
#   /       → 首页
# 注意只开放“音频输出目录”，项目里的 .env（存着 API 密钥）等敏感文件
# 不在这个目录里，从网上访问不到——这是安全底线。
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
config.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(config.AUDIO_DIR)), name="audio")


# ============================================================
# 二、请求体格式（前端发来的数据长什么样）
# ============================================================

class ExtractReq(BaseModel):
    urls: str = ""


class GenerateReq(BaseModel):
    files: list[str] = []
    articles: str = ""
    style: str = "通用"
    word_count: int = 800
    creativity: float = 0.7


class RefineReq(BaseModel):
    script: str = ""
    suggestions: str = ""


class SynthReq(BaseModel):
    script: str = ""
    voice_name: str = ""
    rate: float = 0
    volume: float = 0
    pause: float = 500
    use_sovits: bool = False
    refer_audio: str = ""
    prompt_text: str = ""


# ============================================================
# 三、配音任务管理
# ============================================================
#
# 【为什么要"任务"这一层】
# 配音要跑好几分钟。如果让网页直接干等这几分钟，会遇到两个问题：
#   1. 服务器的"总机"被这一个请求占住，期间界面点什么都没反应；
#   2. 用户看不出任何进度，不知道是在干活还是卡死了。
#
# 所以改成"下单-取号"模式：
#   网页 → 下单（立刻拿到一个任务号）→ 每隔一秒问一次"做好了吗"
#   后端 → 在后台线程里慢慢做，状态记在小本本（SYNTH_TASKS）上
#
SYNTH_TASKS = {}
_SYNTH_LOCK = threading.Lock()


def _reap_tasks():
    """小本本只保留最近 20 条，太老的删掉（防内存里越攒越多）。"""
    if len(SYNTH_TASKS) > 20:
        oldest = sorted(SYNTH_TASKS.items(), key=lambda kv: kv[1].get("started_at", 0))
        for tid, _ in oldest[: len(SYNTH_TASKS) - 20]:
            SYNTH_TASKS.pop(tid, None)


def _run_synth_task(task_id: str, params: dict):
    """
    在后台线程里真正执行配音。做完把结果写进小本本。

    为什么用 asyncio.run：配音函数是个 async 函数（内部用了 await），
    必须在"事件循环"里跑；而后台线程里没有现成的循环，asyncio.run
    会顺手新建一个。这样它就跟网页服务的总机互不干扰了。
    """
    task = SYNTH_TASKS[task_id]
    try:
        task["message"] = "正在准备…"
        audio_path, message, _ = asyncio.run(synthesize_audio(**params))
        if audio_path is None:
            task.update(status="error", message=message or "配音失败")
        else:
            name = Path(audio_path).name
            task.update(
                status="done",
                message=message or "配音完成",
                audio_url=f"/audio/{name}",
                filename=name,
            )
    except Exception as e:
        logger.exception("配音任务出错")
        task.update(status="error", message=f"配音出错：{e}")
    finally:
        task["finished_at"] = time.time()


# ============================================================
# 四、接口（前端照着这些"菜单"点菜）
# ============================================================

@app.get("/")
def home():
    """把网页首页发给浏览器。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/bootstrap")
def api_bootstrap():
    """网页一打开就先要的一批基础信息：声音列表、服务状态、模型名等。"""
    online, message = check_gpt_sovits_status_detailed()
    return {
        "ok": True,
        "voices": get_available_voices(),
        "sovits": {"online": online, "message": message},
        "models": {
            "acoustic": config.SOVITS_WEIGHT.stem,
            "gpt": config.GPT_WEIGHT.stem,
        },
        "outputDir": str(config.AUDIO_DIR),
        "fileTypes": SUPPORTED_EXTENSIONS,
    }


def _is_uploaded_article(path_str: str) -> bool:
    """
    检查传来的文件路径是不是"我们自己的上传文件夹"里的。
    防止有人伪造一个路径让服务器去读不该读的文件（安全起见）。
    """
    try:
        p = Path(path_str).resolve()
        return p.is_file() and UPLOAD_DIR.resolve() in p.parents
    except (OSError, ValueError):
        return False


@app.post("/api/extract")
def api_extract(req: ExtractReq):
    """从网址抓文章正文。"""
    articles, status = extract_from_urls(req.urls)
    return {"ok": True, "articles": articles, "status": status}


@app.post("/api/upload")
async def api_upload(kind: str = "article", files: list[UploadFile] = File(...)):
    """
    接收上传的文件。
    kind=article  文章（.txt/.md/.docx），上传后立刻试读一遍，把结果告诉前端
    kind=audio    参考音频（语音克隆用），只存下来并校验格式
    """
    results = []
    for f in files:
        raw_name = Path(f.filename or "未命名").name
        # 把文件名里 Windows 不允许的字符换成下划线，防止存盘出错
        safe_name = re.sub(r'[\\/:*?"<>|\r\n]', "_", raw_name)
        target = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        data = await f.read()
        target.write_bytes(data)

        item = {"path": str(target), "name": raw_name, "size": len(data)}

        if kind == "audio":
            ext = Path(safe_name).suffix.lower()
            if ext not in AUDIO_EXTS:
                item.update(ok=False, info=f"不支持的音频格式 {ext or '（无扩展名）'}")
            else:
                item.update(ok=True, info=f"{len(data) / 1024:.0f} KB")
        else:
            ok, result = read_one_file(target)
            if ok:
                item.update(ok=True, chars=len(result))
            else:
                item.update(ok=False, info=result)
        results.append(item)

    return {"ok": True, "files": results}


@app.post("/api/generate")
def api_generate(req: GenerateReq):
    """
    生成文案（耗时约半分钟到两分钟，前端要显示"生成中"动效）。
    文件路径先过滤一遍：只认上传文件夹里的，其它一律忽略。
    """
    files = [p for p in (req.files or []) if _is_uploaded_article(p)]
    script = generate_video_script(
        files or None, req.articles, req.style, int(req.word_count), float(req.creativity)
    )
    return {"ok": True, "script": script}


@app.post("/api/refine")
def api_refine(req: RefineReq):
    """按建议润色文案。"""
    script = refine_video_script(req.script, req.suggestions)
    return {"ok": True, "script": script}


@app.get("/api/sovits/status")
def api_sovits_status():
    """
    查一下语音引擎（GPT-SoVITS）服务在不在线。
    注意字段含义：ok = "这次查询成功"，online = "语音服务在线"。
    两回事，别混。
    """
    online, message = check_gpt_sovits_status_detailed()
    return {"ok": True, "online": online, "message": message}


@app.post("/api/sovits/start")
async def api_sovits_start():
    """
    启动语音引擎。这是个"慢动作"——要等 10~30 秒模型加载，
    所以用 SSE（服务器一边干一边把进展推给网页）的方式，让用户看到实时进度。

    名词解释——SSE：可以理解为"后厨直播"。
    师傅（后端）每完成一步就喊一嗓子，前厅（网页）立刻显示出来，
    不用服务员反复跑后厨问"好了没"。
    """
    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def worker():
            # 老函数是个"边跑边报"的生成器，把它每条消息塞进队列，
            # 主流程再从队列里取出来推给网页。
            try:
                for msg in start_gpt_sovits_service():
                    loop.call_soon_threadsafe(queue.put_nowait, {"message": msg})
            except Exception as e:  # noqa: BLE001 —— 任何异常都要让网页知道
                logger.exception("启动语音服务出错")
                loop.call_soon_threadsafe(queue.put_nowait, {"message": f"❌ 启动出错：{e}"})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, {"done": True})

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = await queue.get()
            yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
            if item.get("done"):
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sovits/stop")
def api_sovits_stop():
    """停止语音引擎。"""
    message = stop_gpt_sovits_service()
    return {"ok": True, "message": message}


@app.post("/api/synthesize")
def api_synthesize(req: SynthReq):
    """
    下单一个配音任务，立刻返回任务号。网页拿着任务号去"取件窗口"轮询。
    同一时间只允许一个配音任务在跑（防止连点烧显卡）。
    """
    if not (req.script or "").strip():
        return {"ok": False, "error": "请先准备文案"}

    with _SYNTH_LOCK:
        for t in SYNTH_TASKS.values():
            if t.get("status") == "running":
                return {"ok": False, "error": "已有一个配音任务正在进行，请等它完成"}
        task_id = uuid.uuid4().hex[:12]
        SYNTH_TASKS[task_id] = {
            "status": "running",
            "message": "已开始",
            "started_at": time.time(),
        }
    _reap_tasks()

    # 参考音频路径安全检查：只认上传文件夹里的文件
    refer = (req.refer_audio or "").strip()
    if refer and not _is_uploaded_article(refer):
        refer = ""

    params = dict(
        script=req.script,
        voice_name=req.voice_name,
        rate=req.rate,
        volume=req.volume,
        pause_duration=req.pause,
        use_gpt_sovits=bool(req.use_sovits),
        refer_audio=refer or None,
        prompt_text=req.prompt_text or "",
    )
    threading.Thread(target=_run_synth_task, args=(task_id, params), daemon=True).start()
    return {"ok": True, "task_id": task_id}


@app.get("/api/synthesize/status/{task_id}")
def api_synth_status(task_id: str):
    """取件窗口：问问某个配音任务现在怎么样了。"""
    task = SYNTH_TASKS.get(task_id)
    if task is None:
        return {"ok": False, "error": "任务不存在（可能服务重启过）"}
    return {"ok": True, **task}


# ============================================================
# 五、程序入口
# ============================================================

def main_entry():
    """
    启动流程跟旧版保持一致的习惯：
      - 端口被"自己"占着（重复双击）→ 直接打开已有界面，不报错
      - 端口被别的程序占着 → 打印排查步骤
      - 正常情况 → 启动服务并自动打开浏览器
    """
    import webbrowser

    if is_port_in_use(WEB_PORT):
        if _looks_like_our_app(WEB_PORT):
            print()
            print("=" * 62)
            print("  程序已经在运行了，正在为你打开界面…")
            print("=" * 62)
            print()
            print(f"  如果浏览器没有自动打开，手动访问： http://127.0.0.1:{WEB_PORT}")
            print()
            webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")
            time.sleep(2)
            sys.exit(0)

        print()
        print("=" * 62)
        print(f"  启动失败：端口 {WEB_PORT} 被别的程序占用了")
        print("=" * 62)
        print()
        print("  请依次尝试：")
        print()
        print("   1. 在命令行执行下面两行，查出是谁占的：")
        print(f"        netstat -ano | findstr :{WEB_PORT}")
        print('        tasklist /FI "PID eq 上面显示的数字"')
        print("   2. 确认是不需要的程序后，结束它：")
        print("        taskkill /F /PID 那个数字")
        print("   3. 或者改个端口：在 .env 里加一行  APP_PORT=7861")
        print()
        input("  按回车键退出…")
        sys.exit(1)

    logger.info("新版界面已启动: http://%s:%s", config.APP_HOST, WEB_PORT)

    # 开发调试时设 WEB_NO_BROWSER=1，就不自动弹浏览器
    if not os.environ.get("WEB_NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")).start()

    uvicorn.run(app, host=config.APP_HOST, port=WEB_PORT, log_level="warning")


if __name__ == "__main__":
    main_entry()
