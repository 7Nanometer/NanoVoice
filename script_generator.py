import os
from dotenv import load_dotenv
import traceback

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")

def mock_generate_script(articles, style="通用", word_count=800, creativity=0.7):
    if not articles or all(not art.strip() for art in articles):
        return "请至少输入一篇文章内容"
    
    style_intros = {
        "通用": "大家好，欢迎来到今天的视频！",
        "科技": "各位科技爱好者们，今天来聊聊最新的技术趋势！",
        "生活": "亲爱的朋友们，今天想和大家分享一些生活感悟！",
        "娱乐": "哈喽各位小伙伴，今天给大家带来最新的娱乐资讯！",
        "财经": "各位投资者朋友们，今天来分析一下市场动态！"
    }
    
    content = style_intros[style] + "\n\n"
    
    # 从文章内容中提取关键句子
    article_snippets = []
    for i, art in enumerate(articles):
        if art.strip():
            sentences = art.replace("\n", " ").split("。")
            useful = [s.strip() + "。" for s in sentences if len(s.strip()) > 10]
            if useful:
                for s in useful[:3]:
                    article_snippets.append(s)
    
    if not article_snippets:
        article_snippets = ["文章内容已加载，但未能提取到有效信息。"]
    
    parts = []
    parts.append("今天我们来聊聊这篇文章的核心内容。")
    for snippet in article_snippets:
        if len("".join(parts)) + len(snippet) + len(content) < word_count - 50:
            parts.append(snippet)
        else:
            break
    
    footer = "\n\n感谢观看，别忘了点赞关注！"
    
    # 构建文案
    body = "\n\n".join(parts)
    script = content + body + footer
    
    actual_len = len(script.replace(" ", "").replace("\n", ""))
    if actual_len < word_count:
        fill_count = word_count - actual_len
        fill_text = "这些内容值得我们深入思考，也欢迎大家在评论区分享你的看法。"
        repeats = fill_count // len(fill_text) + 1
        fill = " ".join([fill_text] * repeats)[:fill_count]
        script = script[:-len(footer)] + "\n\n" + fill + footer
    
    return script

def mock_refine_script(script, suggestions):
    return f"""【已根据您的建议润色】

{script}

---
修改说明：根据您的要求"{suggestions}"，已对文案进行优化，使其更加流畅自然。
"""

def generate_script(articles, style="通用", word_count=800, creativity=0.7):
    if not api_key or api_key == "your_openai_api_key_here":
        return mock_generate_script(articles, style, word_count, creativity)
    
    try:
        from openai import OpenAI
        
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        )
        
        articles_text = "\n\n".join([f"文章{i+1}：\n{art[:3000]}" for i, art in enumerate(articles) if art.strip()])
        
        style_prompts = {
            "通用": "口语化、有吸引力的视频口播文案",
            "科技": "专业、严谨的科技类视频口播文案",
            "生活": "亲切、温暖的生活类视频口播文案",
            "娱乐": "活泼、有趣的娱乐类视频口播文案",
            "财经": "专业、权威的财经类视频口播文案"
        }
        
        prompt = f"""你是一个专业的自媒体视频文案创作专家。

请根据以下文章内容，创作一篇{word_count}字的{style_prompts[style]}。

文章内容：
{articles_text}

要求：
1. 必须基于文章内容创作，不得脱离主题
2. 开头吸引眼球，结尾引导互动（点赞、评论、关注）
3. 语言口语化，适合视频口播
4. **严格控制字数在{word_count}字，上下浮动不超过5%**
5. 不要使用Markdown格式

输出文案："""
        
        max_tokens = int(word_count * 1.5)
        
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": f"你是一位专业的视频文案创作助手。严格按照用户要求的字数创作，目标{word_count}字，误差不超过5%。中文一个字算一个token。"},
                {"role": "user", "content": prompt}
            ],
            temperature=creativity,
            max_tokens=max_tokens
        )
        
        result = response.choices[0].message.content.strip()
        actual_length = len(result.replace(" ", "").replace("\n", ""))
        
        for _ in range(2):
            if actual_length < word_count * 0.9 or actual_length > word_count * 1.1:
                adjust_prompt = f"""请将以下文案调整到{word_count}字（当前{actual_length}字）：

{result}

要求：保持内容完整性和主题不变，只调整篇幅。最终字数必须在{word_count}字的±5%范围内。"""
                
                adjust_response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[
                        {"role": "system", "content": f"你是一位专业的文案编辑专家。精确调整文案长度到{word_count}字，保持内容完整性。中文一个字算一个token。"},
                        {"role": "user", "content": adjust_prompt}
                    ],
                    temperature=0.3,
                    max_tokens=int(word_count * 1.5)
                )
                result = adjust_response.choices[0].message.content.strip()
                actual_length = len(result.replace(" ", "").replace("\n", ""))
            else:
                break
        
        return result
    except Exception as e:
        error_msg = f"API调用失败: {str(e)}\n\n{traceback.format_exc()}"
        print(f"ERROR: {error_msg}")
        return f"【API调用失败】\n{error_msg}\n\n已自动切换到演示模式:\n{mock_generate_script(articles, style, word_count, creativity)}"

def refine_script(script, suggestions):
    if not api_key or api_key == "your_openai_api_key_here":
        return mock_refine_script(script, suggestions)
    
    try:
        from openai import OpenAI
        
        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        )
        
        prompt = f"""请根据以下建议对视频文案进行修改润色：

原始文案：
{script}

修改建议：
{suggestions}

请直接输出修改后的文案："""
        
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一位专业的文案润色专家。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"润色失败: {str(e)}\n\n已自动切换到演示模式:\n{mock_refine_script(script, suggestions)}"
