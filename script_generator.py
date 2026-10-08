"""
文案生成模块 —— 把抓来的文章，变成一段适合口播的视频文案。

靠的是调用 DeepSeek 的大模型接口（用 OpenAI 官方的 SDK 来调，因为接口格式兼容）。
如果你没填密钥或者接口调不通，会自动退回"演示模式"——用本地拼凑的假文案，
保证界面还能演示，但会明确告诉你这不是真实结果。
"""

import os
import re
from datetime import datetime

from logger import get_logger

logger = get_logger(__name__)

# 五种风格对应的开场白（演示模式用）和写作要求（真实调用时给大模型的指令）
STYLE_INTROS = {
    "通用": "大家好，欢迎来到今天的视频！",
    "科技": "各位科技爱好者们，今天来聊聊最新的技术趋势！",
    "生活": "亲爱的朋友们，今天想和大家分享一些生活感悟！",
    "娱乐": "哈喽各位小伙伴，今天给大家带来最新的娱乐资讯！",
    "财经": "各位投资者朋友们，今天来分析一下市场动态！",
}

STYLE_PROMPTS = {
    "通用": "口语化、有吸引力的视频口播文案",
    "科技": "专业、严谨的科技类视频口播文案",
    "生活": "亲切、温暖的生活类视频口播文案",
    "娱乐": "活泼、有趣的娱乐类视频口播文案",
    "财经": "专业、权威的财经类视频口播文案",
}

# 给大模型的通用要求。第 5 条是后加的——原来没写这条，
# 结果模型会把"【开头：抓住眼球】"这种结构标记写进正文，配音时被一字不差念出来。
COMMON_RULES = """要求：
1. 必须基于文章内容创作，不得脱离主题
2. 开头吸引眼球，结尾引导互动（点赞、评论、关注）
3. 语言口语化，适合视频口播
4. 严格控制字数在{word_count}字，上下浮动不超过5%
5. 不要使用Markdown格式
6. 【重要】只输出可以直接朗读的正文。不要输出任何【】标记、小标题、序号、"开头/中间/结尾"之类的结构提示，这些会被语音合成一字不差地念出来。"""


def count_chars(text: str) -> int:
    """统计有效字数（不算空格和换行）。"""
    return len(re.sub(r"\s", "", text or ""))


def _save_script(text: str, cfg, kind: str = "文案") -> None:
    """把生成的文案另存一份到 outputs/scripts/，方便写论文时引用。"""
    try:
        path = cfg.SCRIPT_DIR / f"{kind}_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path.write_text(text, encoding="utf-8")
        logger.info("文案已存档：%s", path.name)
    except OSError:
        logger.warning("文案存档失败（不影响使用）", exc_info=True)


def _get_api_key() -> str:
    """
    读取 API 密钥。

    改造前是在文件导入时读一次并存在变量里，所以改了 .env 必须重启程序才生效。
    现在每次调用都重新读，改完立即生效。
    """
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key or key in ("your_openai_api_key_here", "在这里填你的密钥"):
        return ""
    return key


def _client(cfg):
    """创建一个访问大模型的客户端。没配密钥就返回 None。"""
    key = _get_api_key()
    if not key:
        return None

    from openai import OpenAI

    return OpenAI(
        api_key=key,
        base_url=cfg.LLM_BASE_URL,
        timeout=cfg.LLM_TIMEOUT,
        max_retries=2,      # 网络抖动时自动重试两次
    )


def _max_tokens_for(word_count: int) -> int:
    """
    根据目标字数估算"最多让模型输出多少 token"。

    token 是大模型计算长度的单位，中文大约 1 个字 = 0.6~1 个 token。
    留出 1.8 倍余量保证够写，同时封顶 8000（超过模型单次输出上限会被截断）。
    """
    return min(int(word_count * 1.8) + 200, 8000)


# ============================================================
# 演示模式（没有密钥 / 接口调不通时的兜底）
# ============================================================

def mock_generate_script(articles, style="通用", word_count=800):
    """
    本地拼凑一份"假文案"，纯粹为了让界面能演示。

    注意：这**不是**大模型生成的内容，只是把文章里的句子摘出来拼在一起。
    """
    if not articles or all(not a.strip() for a in articles):
        return "请至少输入一篇文章内容"

    intro = STYLE_INTROS.get(style, STYLE_INTROS["通用"])
    content = intro + "\n\n"

    snippets = []
    for art in articles:
        if art.strip():
            sentences = art.replace("\n", " ").split("。")
            useful = [s.strip() + "。" for s in sentences if len(s.strip()) > 10]
            snippets.extend(useful[:3])

    if not snippets:
        snippets = ["文章内容已加载，但未能提取到有效信息。"]

    parts = ["今天我们来聊聊这篇文章的核心内容。"]
    for snippet in snippets:
        if count_chars("".join(parts)) + count_chars(snippet) + count_chars(content) < word_count - 50:
            parts.append(snippet)
        else:
            break

    footer = "\n\n感谢观看，别忘了点赞关注！"
    script = content + "\n\n".join(parts) + footer

    # 字数不够就用一句固定的话凑
    actual = count_chars(script)
    if actual < word_count:
        fill_text = "这些内容值得我们深入思考，也欢迎大家在评论区分享你的看法。"
        repeat = (word_count - actual) // len(fill_text) + 1
        fill = "".join([fill_text] * repeat)
        script = script[: -len(footer)] + "\n\n" + fill + footer

    return script


def mock_refine_script(script, suggestions):
    """
    演示模式下的"润色" —— 其实一个字都没改。

    改造前这里会在开头加一句"【已根据您的建议润色】"，让人误以为真的改了。
    现在改成如实说明。
    """
    return (f"【演示模式：未做实际修改】\n\n{script}\n\n"
            f"———\n说明：当前处于演示模式（没有可用的 AI 接口），"
            f"所以并没有真的按「{suggestions}」修改文案。\n"
            f"请检查 .env 里的 OPENAI_API_KEY 是否填写正确。")


# ============================================================
# 真实调用
# ============================================================

def generate_script(articles, style="通用", word_count=800, creativity=0.7, cfg=None):
    """根据文章生成视频文案。返回文案字符串。"""
    style_name = STYLE_PROMPTS.get(style, STYLE_PROMPTS["通用"])
    client = _client(cfg)

    if client is None:
        logger.warning("未配置 API 密钥，使用演示模式")
        return ("【演示模式】未检测到可用的 AI 接口密钥，下面是本地拼凑的示例文案，"
                "不是真实生成结果。\n请在 .env 里填写 OPENAI_API_KEY。\n\n"
                + mock_generate_script(articles, style, word_count))

    try:
        articles_text = "\n\n".join(
            f"文章{i + 1}：\n{art[:cfg.ARTICLE_MAX_CHARS]}"
            for i, art in enumerate(articles) if art.strip()
        )

        prompt = (f"你是一个专业的自媒体视频文案创作专家。\n\n"
                  f"请根据以下文章内容，创作一篇{word_count}字的{style_name}。\n\n"
                  f"文章内容：\n{articles_text}\n\n"
                  + COMMON_RULES.format(word_count=word_count)
                  + "\n\n输出文案：")

        response = client.chat.completions.create(
            model=cfg.LLM_MODEL,
            messages=[
                {"role": "system",
                 "content": f"你是一位专业的视频文案创作助手，目标字数{word_count}字，误差不超过5%。"},
                {"role": "user", "content": prompt},
            ],
            temperature=creativity,
            max_tokens=_max_tokens_for(word_count),
        )
        result = (response.choices[0].message.content or "").strip()
        actual = count_chars(result)

        # 字数不达标就让它再改一次（最多一次，改造前是最多两次——同样的提示词连发三遍很浪费额度）
        if actual < word_count * 0.9 or actual > word_count * 1.1:
            logger.info("字数 %s 偏离目标 %s，请求模型调整一次", actual, word_count)
            adjust = client.chat.completions.create(
                model=cfg.LLM_MODEL,
                messages=[
                    {"role": "system", "content": f"你是一位专业的文案编辑，请精确调整文案长度到{word_count}字。"},
                    {"role": "user",
                     "content": f"请将以下文案调整到{word_count}字（当前{actual}字）：\n\n{result}\n\n"
                                f"保持内容完整性和主题不变，只调整篇幅。"
                                f"只输出调整后的正文，不要任何说明文字。"},
                ],
                temperature=0.3,
                max_tokens=_max_tokens_for(word_count),
            )
            adjusted = (adjust.choices[0].message.content or "").strip()
            if adjusted:
                result = adjusted
                actual = count_chars(result)

        logger.info("文案生成完成：%s 字（目标 %s）", actual, word_count)
        _save_script(result, cfg)
        return result

    except Exception as e:
        # 改造前这里会把完整的报错堆栈（含本机绝对路径）直接显示在界面上，
        # 对使用者毫无帮助还泄露信息。现在详细内容只进日志，界面只说人话。
        logger.exception("调用大模型生成文案失败")
        hint = _friendly_error(e)
        return (f"【AI 接口调用失败】{hint}\n\n"
                f"下面是本地拼凑的示例文案，不是真实生成结果：\n\n"
                + mock_generate_script(articles, style, word_count))


def refine_script(script, suggestions, cfg=None):
    """根据用户给的建议，让大模型改写文案。"""
    if not suggestions or not suggestions.strip():
        return script

    client = _client(cfg)
    if client is None:
        return mock_refine_script(script, suggestions)

    try:
        response = client.chat.completions.create(
            model=cfg.LLM_MODEL,
            messages=[
                {"role": "system",
                 "content": "你是一位专业的文案润色专家。只输出修改后的正文，不要任何说明文字。"},
                {"role": "user",
                 "content": f"请根据以下建议对视频文案进行修改润色：\n\n"
                            f"原始文案：\n{script}\n\n"
                            f"修改建议：\n{suggestions}\n\n"
                            f"要求：保持原意和篇幅大体不变，只按建议调整。"
                            f"不要输出【】标记或任何说明文字。\n\n"
                            f"请直接输出修改后的文案："},
            ],
            temperature=0.5,
            # 改造前这里没设上限，长文案可能被服务端默认上限从中间截断，用户还看不出来
            max_tokens=_max_tokens_for(count_chars(script)),
        )
        result = (response.choices[0].message.content or "").strip()
        if not result:
            return script
        logger.info("文案润色完成")
        _save_script(result, cfg, kind="润色后")
        return result

    except Exception as e:
        logger.exception("调用大模型润色失败")
        return f"【润色失败】{_friendly_error(e)}\n\n以下是原文案（未修改）：\n\n{script}"


def _friendly_error(e: Exception) -> str:
    """把技术报错翻译成普通人能看懂的话。"""
    text = str(e)
    if "401" in text or "Authentication" in text or "api key" in text.lower():
        return "密钥无效。请检查 .env 里的 OPENAI_API_KEY 是否填写正确、是否已过期。"
    if "402" in text or "Insufficient Balance" in text or "余额" in text:
        return "账户余额不足，请去 DeepSeek 官网充值。"
    if "429" in text or "rate limit" in text.lower():
        return "请求太频繁或已达额度上限，等一会儿再试。"
    if "timeout" in text.lower() or "timed out" in text.lower():
        return "请求超时（接口响应太慢），请重试。"
    if "Connection" in text or "connect" in text.lower():
        return "连不上接口服务器，请检查网络。"
    return f"错误信息：{text}"
