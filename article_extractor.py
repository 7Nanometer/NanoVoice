import requests
from bs4 import BeautifulSoup
import re

def extract_text_from_url(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.encoding = response.apparent_encoding
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for script in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            script.decompose()
        
        text = soup.get_text(separator='\n')
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        cleaned_text = '\n'.join(lines)
        
        return cleaned_text[:5000]
    except Exception as e:
        return f"提取失败: {str(e)}"

def clean_text(text):
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'【[^】】]*】', '', text)
    text = re.sub(r'「[^」」]*」', '', text)
    text = re.sub(r'（[^））]*）', '', text)
    return text.strip()

def split_long_text(text, max_length=2000):
    chunks = []
    current_chunk = ""
    paragraphs = text.split('\n')
    
    for paragraph in paragraphs:
        if len(current_chunk) + len(paragraph) < max_length:
            current_chunk += paragraph + '\n'
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = paragraph + '\n'
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks
