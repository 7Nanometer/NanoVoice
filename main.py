import gradio as gr
import asyncio
import os
import subprocess
import threading
import socket
from article_extractor import extract_text_from_url
from script_generator import generate_script, refine_script
from speech_synthesizer import generate_audio_from_script, get_available_voices, check_gpt_sovits_status, check_gpt_sovits_status_detailed

gpt_sovits_process = None
gpt_sovits_start_time = None

def is_port_in_use(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0
    except:
        return False

def kill_process_on_port(port):
    try:
        result = subprocess.run(
            ['netstat', '-ano'],
            capture_output=True, text=True, timeout=5
        )
        pids_to_kill = set()
        for line in result.stdout.splitlines():
            if f':{port}' in line and 'LISTENING' in line:
                parts = line.split()
                if len(parts) >= 5:
                    pids_to_kill.add(parts[-1])
        
        killed = False
        for pid in pids_to_kill:
            try:
                subprocess.run(
                    ['taskkill', '/F', '/PID', pid],
                    capture_output=True, timeout=5
                )
                killed = True
            except:
                pass
        return killed
    except:
        return False

def start_gpt_sovits_service():
    global gpt_sovits_process, gpt_sovits_start_time
    
    if check_gpt_sovits_status():
        return "GPT-SoVITS 服务: ✅ 已连接"
    
    if gpt_sovits_process is not None and gpt_sovits_process.poll() is None:
        return "GPT-SoVITS 服务正在启动中，请稍候...\n点击「🔄 检查状态」查看最新状态"
    
    try:
        if is_port_in_use(9880):
            kill_process_on_port(9880)
            import time
            time.sleep(2)
            if is_port_in_use(9880):
                return "❌ 端口9880仍被占用，请手动关闭占用进程后重试\n可执行: netstat -ano | findstr :9880 获取PID，然后 taskkill /F /PID <PID>"
        
        sovits_dir = r"G:\GPT-SoVITS-v2pro-20250604"
        python_exec = os.path.join(sovits_dir, "runtime", "python.exe")
        api_script = os.path.join(sovits_dir, "api.py")
        
        if not os.path.exists(python_exec):
            return f"❌ 错误：找不到Python执行器 {python_exec}"
        if not os.path.exists(api_script):
            return f"❌ 错误：找不到API脚本 {api_script}"
        
        args = [
            python_exec,
            api_script,
            "-d", "cuda",
            "-s", r"G:\GPT-SoVITS-v2pro-20250604\GPT_SoVITS\pretrained_models\v2Pro\s2Gv2Pro.pth",
            "-g", r"G:\GPT-SoVITS-v2pro-20250604\GPT_SoVITS\pretrained_models\s1v3.ckpt",
            "-dr", r"G:\GPT-SoVITS-v2pro-20250604\logs\7Nanometer\5-wav32k\阿基拉.mp3_0000000000_0000310400.wav",
            "-dt", "测试语音克隆",
            "-dl", "zh"
        ]
        
        def run_service():
            global gpt_sovits_process
            gpt_sovits_process = subprocess.Popen(
                args,
                cwd=sovits_dir,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            gpt_sovits_process.wait()
        
        thread = threading.Thread(target=run_service, daemon=True)
        thread.start()
        
        import time
        gpt_sovits_start_time = time.time()
        
        return "🚀 GPT-SoVITS 服务启动命令已发送...\n\n服务启动需要 10-30 秒（加载模型），请耐心等待。\n点击「🔄 检查状态」确认连接。\n\n注意：会弹出一个新的命令行窗口显示服务日志。"
    
    except Exception as e:
        return f"❌ 启动 GPT-SoVITS 服务失败: {str(e)}"

def stop_gpt_sovits_service():
    global gpt_sovits_process, gpt_sovits_start_time
    
    try:
        if gpt_sovits_process is not None and gpt_sovits_process.poll() is None:
            gpt_sovits_process.terminate()
            gpt_sovits_process = None
            gpt_sovits_start_time = None
            import time
            time.sleep(1)
    
    except Exception as e:
        return f"停止服务失败: {str(e)}"
    
    if is_port_in_use(9880):
        kill_process_on_port(9880)
    
    gpt_sovits_process = None
    gpt_sovits_start_time = None
    
    if not check_gpt_sovits_status():
        return "GPT-SoVITS 服务已停止"
    
    return "GPT-SoVITS 服务可能仍在运行，请点击「🔄 检查状态」确认"

def extract_from_urls(urls_text):
    urls = [url.strip() for url in urls_text.split('\n') if url.strip()]
    articles = []
    for url in urls:
        if url.startswith('http'):
            content = extract_text_from_url(url)
            articles.append(content)
        else:
            articles.append(f"无效URL: {url}")
    return "\n\n---\n\n".join(articles)

def generate_video_script(articles_text, style, word_count, creativity):
    articles = [art.strip() for art in articles_text.split('---') if art.strip()]
    if not articles:
        return "请输入文章内容或URL"
    
    script = generate_script(articles, style, word_count, creativity)
    return script

def refine_video_script(script, suggestions):
    if not script.strip():
        return "请先生成文案"
    return refine_script(script, suggestions)

async def synthesize_audio(script, voice_name, rate, volume, pause_duration, use_gpt_sovits, refer_audio, prompt_text):
    print(f"[DEBUG] synthesize_audio called")
    print(f"[DEBUG] Script length: {len(script)}")
    print(f"[DEBUG] Script first 200 chars: {script[:200]}")
    print(f"[DEBUG] voice_name: {voice_name}")
    print(f"[DEBUG] use_gpt_sovits: {use_gpt_sovits}")
    print(f"[DEBUG] refer_audio: {refer_audio}")
    print(f"[DEBUG] prompt_text: {prompt_text}")
    
    if not script.strip():
        return None, "请先生成文案", None
    
    voices = get_available_voices()
    voice_info = next((v for v in voices if v["name"] == voice_name), {"voice": "zh-CN-YunxiNeural"})
    voice = voice_info["voice"]
    
    rate_str = f"+{rate}%" if rate > 0 else f"{rate}%"
    volume_str = f"+{volume}%" if volume > 0 else f"{volume}%"
    
    output_file = "final_audio.mp3"
    
    is_gpt_sovits_voice = (voice == "gpt_sovits")
    should_use_gpt_sovits = is_gpt_sovits_voice or use_gpt_sovits
    print(f"[DEBUG] should_use_gpt_sovits: {should_use_gpt_sovits}")
    
    try:
        final_refer_audio = refer_audio
        
        if should_use_gpt_sovits:
            if not check_gpt_sovits_status():
                return None, "GPT-SoVITS 服务未启动，请先点击「🚀 启动服务」按钮启动服务", None
            if not refer_audio:
                return None, "使用 GPT-SoVITS 语音克隆需要上传参考音频", None
            if not prompt_text.strip():
                return None, "使用 GPT-SoVITS 语音克隆需要填写参考音频对应的文本", None
            
            sovits_dir = r"G:\GPT-SoVITS-v2pro-20250604"
            final_refer_audio = os.path.join(sovits_dir, "refer_audio_temp.wav")
            
            if isinstance(refer_audio, str) and os.path.exists(refer_audio):
                import shutil
                shutil.copy(refer_audio, final_refer_audio)
                print(f"[DEBUG] Copied refer audio from {refer_audio} to {final_refer_audio}")
            elif isinstance(refer_audio, tuple):
                import numpy as np
                from pydub import AudioSegment
                audio_array = refer_audio[0]
                sample_rate = refer_audio[1]
                audio = AudioSegment(
                    audio_array.tobytes(),
                    frame_rate=sample_rate,
                    sample_width=audio_array.dtype.itemsize,
                    channels=1
                )
                audio.export(final_refer_audio, format="wav")
                print(f"[DEBUG] Exported refer audio to {final_refer_audio}")
            
            result = await generate_audio_from_script(
                script, output_file, voice, rate_str, volume_str, 
                pause_duration, True, final_refer_audio, prompt_text
            )
        else:
            result = await generate_audio_from_script(
                script, output_file, voice, rate_str, volume_str, pause_duration
            )
        
        if isinstance(result, tuple):
            if result[0] is None:
                return None, result[1], None
            else:
                return result[0], result[1], result[0]
        else:
            return result, None, result
    except Exception as e:
        return None, f"语音合成异常: {str(e)}", None

def count_words(text):
    return len(text.replace(" ", "").replace("\n", ""))

def check_sovits_status():
    status, message = check_gpt_sovits_status_detailed()
    if status:
        return f"GPT-SoVITS 服务: ✅ 已连接\n状态详情: {message}"
    else:
        return f"GPT-SoVITS 服务: ❌ 未连接\n原因: {message}"

with gr.Blocks(title="视频文案自动生成与配音系统") as demo:
    gr.Markdown("# 🎬 多源文本融合与语音克隆配音系统")
    gr.Markdown("输入多篇文章，自动生成视频文案，并使用AI语音合成配音")
    
    with gr.Tab("文案生成"):
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 输入文章")
                url_input = gr.Textbox(label="URL列表（每行一个）", placeholder="https://example.com/article1\nhttps://example.com/article2", lines=5)
                url_button = gr.Button("🔗 从URL提取文章", variant="primary")
                
                articles_input = gr.Textbox(label="或直接粘贴文章内容（用---分隔多篇）", placeholder="文章1内容...\n\n---\n\n文章2内容...", lines=10)
                
                with gr.Row():
                    style_dropdown = gr.Dropdown(["通用", "科技", "生活", "娱乐", "财经"], label="文案风格", value="通用")
                    word_count_slider = gr.Slider(minimum=300, maximum=5000, step=100, label="目标字数", value=800)
                
                creativity_slider = gr.Slider(minimum=0.1, maximum=1.0, step=0.1, label="创意程度", value=0.7)
                
                generate_button = gr.Button("✨ 生成文案", variant="primary", size="lg")
                
            with gr.Column(scale=1):
                gr.Markdown("### 生成的文案")
                script_output = gr.Textbox(label="视频文案", placeholder="生成的文案将显示在这里...", lines=15, interactive=True)
                
                with gr.Row():
                    word_count_display = gr.Textbox(label="字数统计", interactive=False)
                    refresh_count_button = gr.Button("🔄 刷新统计")
                
                refine_suggestions = gr.Textbox(label="修改建议", placeholder="输入修改建议，如：'更口语化一些'、'增加幽默感'", lines=3)
                refine_button = gr.Button("✏️ 润色文案")
                
                regenerate_button = gr.Button("🔄 重新生成")
    
    with gr.Tab("语音合成"):
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 配音设置")
                
                voice_dropdown = gr.Dropdown(
                    choices=[v["name"] for v in get_available_voices()],
                    label="选择声音",
                    value="云希"
                )
                
                with gr.Row():
                    rate_slider = gr.Slider(minimum=-50, maximum=50, step=5, label="语速 (%)", value=0)
                    volume_slider = gr.Slider(minimum=-20, maximum=20, step=1, label="音量 (%)", value=0)
                
                pause_slider = gr.Slider(minimum=200, maximum=1500, step=100, label="句间停顿 (ms)", value=500)
                
                gr.Markdown("---")
                gr.Markdown("### 🔊 GPT-SoVITS 语音克隆")
                
                sovits_status = gr.Textbox(label="服务状态", value=check_sovits_status(), interactive=False)
                
                with gr.Row():
                    start_sovits_button = gr.Button("🚀 启动服务", variant="primary")
                    stop_sovits_button = gr.Button("⏹️ 停止服务")
                    check_status_button = gr.Button("🔄 检查状态")
                
                use_gpt_sovits = gr.Checkbox(label="使用GPT-SoVITS克隆声音", value=False)
                gr.Markdown("*提示：启用语音克隆需要①启动服务 ②上传参考音频 ③填写音频对应的文本，三者缺一不可*")
                
                refer_audio_input = gr.Audio(label="参考音频（您的声音样本）", type="filepath")
                prompt_text_input = gr.Textbox(label="参考音频对应的文本", placeholder="请输入参考音频中您所说的文本内容", lines=3)
                
                synthesize_button = gr.Button("🎤 合成配音", variant="primary", size="lg")
                
            with gr.Column(scale=1):
                gr.Markdown("### 音频预览")
                audio_output = gr.Audio(label="合成的音频", type="filepath")
                status_output = gr.Textbox(label="状态", interactive=False)
                
                download_file = gr.File(label="下载音频文件", visible=True)
    
    url_button.click(extract_from_urls, inputs=url_input, outputs=articles_input)
    
    generate_button.click(
        generate_video_script,
        inputs=[articles_input, style_dropdown, word_count_slider, creativity_slider],
        outputs=script_output
    )
    
    refresh_count_button.click(
        count_words,
        inputs=script_output,
        outputs=word_count_display
    )
    
    script_output.change(
        count_words,
        inputs=script_output,
        outputs=word_count_display
    )
    
    refine_button.click(
        refine_video_script,
        inputs=[script_output, refine_suggestions],
        outputs=script_output
    )
    
    regenerate_button.click(
        generate_video_script,
        inputs=[articles_input, style_dropdown, word_count_slider, creativity_slider],
        outputs=script_output
    )
    
    start_sovits_button.click(
        start_gpt_sovits_service,
        outputs=sovits_status
    )
    
    stop_sovits_button.click(
        stop_gpt_sovits_service,
        outputs=sovits_status
    )
    
    check_status_button.click(
        check_sovits_status,
        outputs=sovits_status
    )
    
    synthesize_button.click(
        synthesize_audio,
        inputs=[script_output, voice_dropdown, rate_slider, volume_slider, pause_slider, use_gpt_sovits, refer_audio_input, prompt_text_input],
        outputs=[audio_output, status_output, download_file]
    )
    
    

if __name__ == "__main__":
    import os
    os.environ["no_proxy"] = "localhost,127.0.0.1"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        allowed_paths=["."],
        share=False,
        inbrowser=True,
        prevent_thread_lock=True
    )
    
    import time
    while True:
        time.sleep(1)
