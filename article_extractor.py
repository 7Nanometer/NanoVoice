"""
网页正文抓取模块 —— 给一个网址，把里面的文章正文抠出来。

原理很简单，分三步：
    1. 下载网页源代码（用 requests 这个库，相当于程序版的"浏览器"）
    2. 解析源代码（用 BeautifulSoup，它能把 HTML 标签树变成可以查找的结构）
    3. 扔掉没用的部分，留下正文文字

注意：这个模块只抓"有权限公开抓取"的网页。使用时请遵守目标网站的规定。
"""

import requests
from bs4 import BeautifulSoup

from logger import get_logger

logger = get_logger(__name__)

# 假装自己是浏览器。
# 很多网站看到"这是程序"就直接拒绝访问，所以带上这个身份标识。
# 这是通用的做法，不涉及伪造他人身份。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 页面上这些标签里的内容不是正文，全部丢掉
USELESS_TAGS = ["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]


def extract_text_from_url(url, cfg):
    """
    抓取一个网址的正文。

    返回值是 (是否成功, 内容或错误说明)：
        成功 → (True,  "正文文字……")
        失败 → (False, "失败原因")
    改造前失败时直接返回一句 "提取失败: xxx" 当正文，
    结果这段报错文字会被当成文章喂给大模型。现在改成明确区分成功和失败。
    """
    try:
        logger.info("开始抓取：%s", url)

        # timeout=(连接超时, 读取超时)，单位秒
        response = requests.get(url, headers=HEADERS, timeout=(5, 30))

        # 检查状态码。403（禁止访问）、404（页面不存在）这类错误页，
        # 改造前会被当成正文照抓不误，抓回来一堆"404 Not Found"。
        response.raise_for_status()

        # 确认返回的确实是网页，而不是图片、PDF 之类
        content_type = response.headers.get("content-type", "")
        if content_type and not any(k in content_type.lower() for k in ("html", "text", "xml")):
            return False, f"这个链接返回的不是网页内容（类型：{content_type}）"

        # —— 处理编码（防止中文乱码）——
        # 网页的文字编码有 UTF-8、GBK 等多种。requests 会先看服务器有没有声明。
        # 改造前是无脑用 apparent_encoding（靠猜），经常把本来正确的 UTF-8 猜成别的，
        # 结果整篇文章乱码。现在只在服务器"没说"或"说得明显不对"的时候才猜。
        if not response.encoding or response.encoding.lower() in ("iso-8859-1", "latin-1"):
            response.encoding = response.apparent_encoding or "utf-8"

        soup = BeautifulSoup(response.text, "html.parser")

        # 丢掉脚本、样式、导航栏、页脚这些非正文部分
        for tag in soup(USELESS_TAGS):
            tag.decompose()

        # 优先找网页里明确标记为"文章主体"的区域，找不到再退回整页
        # （<article> 和 <main> 是网页标准里专门用来标记正文的标签）
        container = soup.find("article") or soup.find("main") or soup

        text = container.get_text(separator="\n")
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        cleaned = "\n".join(lines)

        if len(cleaned) < 50:
            return False, ("抓到的内容太少了（可能这个网页是用 JavaScript 动态加载的，"
                           "或者需要登录才能看）。")

        if len(cleaned) > cfg.FETCH_MAX_CHARS:
            logger.info("正文较长（%s 字），截取前 %s 字", len(cleaned), cfg.FETCH_MAX_CHARS)
            cleaned = cleaned[:cfg.FETCH_MAX_CHARS]

        logger.info("抓取成功：%s（%s 字）", url, len(cleaned))
        return True, cleaned

    except requests.exceptions.HTTPError as e:
        # 服务器明确拒绝：403 没权限、404 不存在、等
        code = e.response.status_code if e.response is not None else "?"
        hint = {
            403: "对方拒绝访问（可能需要登录，或不允许程序抓取）",
            404: "页面不存在（网址可能写错了）",
            429: "请求太频繁，被对方限流了",
            500: "对方服务器出错了",
        }.get(code, "对方返回了错误")
        return False, f"HTTP {code}：{hint}"

    except requests.exceptions.ConnectionError:
        return False, "连不上这个网址（检查一下网址对不对，或网络是否正常）"

    except requests.exceptions.Timeout:
        return False, "访问超时（对方网站响应太慢）"

    except Exception as e:
        logger.exception("抓取出现意外错误：%s", url)
        return False, f"抓取出错：{e}"
