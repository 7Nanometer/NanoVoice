import asyncio
import edge_tts
import requests
import json
import os
from pydub import AudioSegment
import shutil

GPT_SOVITS_API_URL = "http://127.0.0.1:9880"

os.environ['no_proxy'] = 'localhost,127.0.0.1'
os.environ['NO_PROXY'] = 'localhost,127.0.0.1'

session = requests.Session()
session.trust_env = False

async def text_to_speech_edge(text, output_file="output.mp3", voice="zh-CN-YunxiNeural", rate="+0%", volume="+0%"):
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(output_file)
    return output_file

async def text_to_speech_gpt_sovits(text, output_file="output.wav", refer_wav_path="", prompt_text="", prompt_language="all_zh"):
    try:
        payload = {
            "text": text,
            "text_language": "all_zh",
            "top_k": 30,
            "top_p": 0.8,
            "temperature": 0.8,
            "speed": 1.0,
            "sample_steps": 32,
            "cut_punc": ""
        }
        
        if refer_wav_path and prompt_text:
            payload["refer_wav_path"] = refer_wav_path
            payload["prompt_text"] = prompt_text
            payload["prompt_language"] = prompt_language
        
        print(f"[DEBUG] ===== GPT-SoVITS API Request =====")
        print(f"[DEBUG] Text to synthesize ({len(text)} chars): {text[:200]}...")
        print(f"[DEBUG] refer_wav_path: {refer_wav_path}")
        print(f"[DEBUG] refer_wav_path exists: {os.path.exists(refer_wav_path) if refer_wav_path else 'not provided'}")
        print(f"[DEBUG] prompt_text: {prompt_text}")
        print(f"[DEBUG] Full payload keys: {list(payload.keys())}")
        
        for attempt in range(3):
            try:
                print(f"[DEBUG] Attempt {attempt+1}: Making POST request to {GPT_SOVITS_API_URL}/")
                response = session.post(GPT_SOVITS_API_URL + "/", json=payload, timeout=120)
                
                print(f"[DEBUG] Response status: {response.status_code}")
                
                if response.status_code == 200:
                    content_type = response.headers.get('content-type', '')
                    content_length = response.headers.get('content-length', 'N/A')
                    print(f"[DEBUG] Content-Type: {content_type}")
                    print(f"[DEBUG] Content-Length: {content_length}")
                    print(f"[DEBUG] Response content size: {len(response.content)} bytes")
                    
                    with open(output_file, "wb") as f:
                        f.write(response.content)
                    
                    if os.path.exists(output_file):
                        file_size = os.path.getsize(output_file)
                        print(f"[DEBUG] Audio saved to {output_file}, file size: {file_size} bytes")
                        
                        try:
                            test_audio = AudioSegment.from_file(output_file, format="wav")
                            print(f"[DEBUG] Audio duration: {len(test_audio)}ms, frame_rate: {test_audio.frame_rate}")
                        except Exception as e:
                            print(f"[DEBUG] Failed to read audio file: {e}")
                    else:
                        print(f"[DEBUG] ERROR: File not created!")
                    
                    return output_file
                else:
                    try:
                        error_info = response.json()
                        error_msg = error_info.get("message", str(error_info))
                    except:
                        error_msg = response.text[:500]
                    print(f"[ERROR] GPT-SoVITS API error: {error_msg}")
                    return None, f"GPT-SoVITS API错误 (状态码 {response.status_code}): {error_msg}"
            except requests.exceptions.ConnectionError as e:
                print(f"[ERROR] Connection error: {e}")
                if attempt < 2:
                    import time
                    time.sleep(2)
                    continue
                return None, "GPT-SoVITS 服务连接失败，请确保服务已启动"
            except requests.exceptions.Timeout as e:
                print(f"[ERROR] Timeout: {e}")
                if attempt < 2:
                    continue
                return None, "GPT-SoVITS 请求超时，请稍后重试"
            except Exception as e:
                print(f"[ERROR] Exception: {e}")
                if attempt < 2:
                    import time
                    time.sleep(2)
                    continue
                return None, f"GPT-SoVITS调用失败: {str(e)}"
        
        return None, "GPT-SoVITS调用失败: 重试3次后仍无法完成"
    except Exception as e:
        print(f"[ERROR] Unexpected exception: {e}")
        return None, f"GPT-SoVITS调用失败: {str(e)}"

def split_text_into_chunks(text, max_length=300):
    sentences = []
    current_sentence = ""
    
    for char in text:
        current_sentence += char
        if char in ["。", "！", "？", "；", "\n"] and len(current_sentence) > 10:
            sentences.append(current_sentence.strip())
            current_sentence = ""
    
    if current_sentence.strip():
        sentences.append(current_sentence.strip())
    
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) <= max_length:
            current_chunk += sentence
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = sentence
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks

async def generate_audio_from_script(script, output_file="final_output.mp3", voice="zh-CN-YunxiNeural", rate="+0%", volume="+0%", pause_duration=500, use_gpt_sovits=False, refer_wav_path="", prompt_text=""):
    print(f"[DEBUG] ========================================")
    print(f"[DEBUG] generate_audio_from_script called")
    print(f"[DEBUG] use_gpt_sovits={use_gpt_sovits}")
    print(f"[DEBUG] Script length: {len(script)}")
    print(f"[DEBUG] Script content (first 200 chars): {script[:200]}")
    print(f"[DEBUG] Script content (last 200 chars): {script[-200:]}")
    print(f"[DEBUG] refer_wav_path: {refer_wav_path}")
    print(f"[DEBUG] prompt_text: {prompt_text}")
    print(f"[DEBUG] ========================================")
    
    chunks = split_text_into_chunks(script)
    
    if not chunks:
        return None, "脚本内容为空，无法生成音频"
    
    print(f"[DEBUG] Split into {len(chunks)} chunks:")
    for i, chunk in enumerate(chunks):
        print(f"[DEBUG]   Chunk {i+1}: {len(chunk)} chars - \"{chunk[:80]}...\"")
    
    if use_gpt_sovits and (not refer_wav_path or not prompt_text):
        return None, "使用GPT-SoVITS需要提供参考音频和提示文本"
    
    audio_segments = []
    edge_tts_failed = False
    
    for i, chunk in enumerate(chunks):
        temp_file = f"temp_{i}.{'wav' if use_gpt_sovits else 'mp3'}"
        
        try:
            if use_gpt_sovits:
                print(f"[DEBUG] >>> Processing chunk {i+1}/{len(chunks)}: \"{chunk[:100]}...\"")
                result = await text_to_speech_gpt_sovits(chunk, temp_file, refer_wav_path, prompt_text, "all_zh")
                if isinstance(result, tuple):
                    return None, result[1]
            else:
                print(f"[DEBUG] Using Edge TTS for chunk {i}")
                try:
                    await text_to_speech_edge(chunk, temp_file, voice, rate, volume)
                except Exception as e:
                    print(f"[DEBUG] Edge TTS failed: {e}")
                    if check_gpt_sovits_status() and refer_wav_path and prompt_text:
                        print(f"[DEBUG] Falling back to GPT-SoVITS")
                        edge_tts_failed = True
                        result = await text_to_speech_gpt_sovits(chunk, temp_file, refer_wav_path, prompt_text, "all_zh")
                        if isinstance(result, tuple):
                            return None, result[1]
                    else:
                        raise e
            
            if not os.path.exists(temp_file):
                return None, f"第{i+1}段音频生成失败：文件未创建"
            
            audio = AudioSegment.from_file(temp_file, format="wav" if use_gpt_sovits or edge_tts_failed else "mp3")
            expected_duration_ms = len(chunk) * 250  # 中文约4字/秒 = 250ms/字
            actual_duration_ms = len(audio)
            print(f"[DEBUG] Chunk {i+1} audio: {audio.frame_rate}Hz, {audio.sample_width*8}bit, {audio.channels}ch")
            print(f"[DEBUG] Chunk {i+1} duration: {actual_duration_ms}ms (text: {len(chunk)} chars, expected ~{expected_duration_ms}ms)")
            print(f"[DEBUG] Chunk {i+1} speed ratio: {actual_duration_ms / expected_duration_ms:.2f}x (1.0 = normal)")
            audio_segments.append(audio)
            
            if i < len(chunks) - 1:
                silence = AudioSegment.silent(duration=pause_duration, frame_rate=audio.frame_rate)
                audio_segments.append(silence)
            
        except Exception as e:
            return None, f"第{i+1}段音频生成失败: {str(e)}"
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
    
    if audio_segments:
        try:
            combined = audio_segments[0]
            for seg in audio_segments[1:]:
                combined = combined + seg
            
            print(f"[DEBUG] Combined audio: {combined.frame_rate}Hz, {combined.sample_width*8}bit, {combined.channels}ch, {len(combined)}ms")
            
            combined = combined.set_sample_width(2)
            combined = combined.set_channels(1)
            
            combined.export(output_file, format="mp3", bitrate="192k", parameters=["-q:a", "0"])
            
            print(f"[DEBUG] Exported to {output_file}, file size: {os.path.getsize(output_file)} bytes")
            
            if not os.path.exists(output_file):
                return None, "音频导出失败：输出文件未创建"
            
            message = None
            if edge_tts_failed:
                message = "Edge TTS网络连接失败，已自动切换到GPT-SoVITS"
            return output_file, message
        except Exception as e:
            return None, f"音频合并导出失败: {str(e)}"
    else:
        return None, "没有生成任何音频内容"

def get_available_voices():
    voices = [
        {"name": "云希", "voice": "zh-CN-YunxiNeural"},
        {"name": "晓晓", "voice": "zh-CN-XiaoxiaoNeural"},
        {"name": "晓辰", "voice": "zh-CN-XiaochenNeural"},
        {"name": "晓莉", "voice": "zh-CN-XiaoliNeural"},
        {"name": "晓梅", "voice": "zh-CN-XiaomeiNeural"},
        {"name": "晓楠", "voice": "zh-CN-XiaonanNeural"},
        {"name": "晓婷", "voice": "zh-CN-XiaotingNeural"},
        {"name": "晓宇", "voice": "zh-CN-XiaoyuNeural"},
        {"name": "云帆", "voice": "zh-CN-YunfanNeural"},
        {"name": "云扬", "voice": "zh-CN-YunyangNeural"},
        {"name": "GPT-SoVITS (我的声音)", "voice": "gpt_sovits"}
    ]
    return voices

def check_gpt_sovits_status():
    try:
        response = session.get(GPT_SOVITS_API_URL + "/?text=test&text_language=all_zh", timeout=5)
        if response.status_code == 200:
            return True
        if response.status_code == 400:
            return True
        return False
    except requests.exceptions.ConnectionError:
        return False
    except requests.exceptions.Timeout:
        return False
    except Exception:
        return False

def check_gpt_sovits_status_detailed():
    try:
        response = session.get(GPT_SOVITS_API_URL + "/?text=test&text_language=all_zh", timeout=5)
        if response.status_code == 200:
            return True, "服务正常，返回音频数据"
        if response.status_code == 400:
            return True, f"服务正常，返回错误: {response.text[:200]}"
        return False, f"服务返回未知状态码: {response.status_code}"
    except requests.exceptions.ConnectionError as e:
        return False, f"连接失败: {str(e)}"
    except requests.exceptions.Timeout as e:
        return False, f"请求超时: {str(e)}"
    except Exception as e:
        return False, f"未知错误: {str(e)}"
