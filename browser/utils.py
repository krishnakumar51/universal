import time
from pathlib import Path
from PIL import Image
from bs4 import BeautifulSoup

def get_current_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def resize_image_if_needed(image_path: Path):
    try:
        with Image.open(image_path) as img:
            if max(img.size) > 1024:
                img.thumbnail((1024, 1024), Image.LANCZOS)
                img.save(image_path)
    except Exception as e:
        print(f"Warning: Could not resize image {image_path}. Error: {e}")

def simplify_page_for_llm(page_content: str) -> tuple[str, str]:
    soup = BeautifulSoup(page_content, "html.parser")
    
    # FIX: Remove destructive decompose; only remove non-essential if needed (e.g., scripts/styles break JS, so skip)
    # for tag in soup.find_all(['script', 'style', 'svg']):  # Optional: Uncomment if you want to remove these, but test
    #     tag.decompose()

    interactive_elements = soup.find_all(['a', 'button', 'input', 'textarea', 'select'])
    simplified_elements = []
    
    for i, element in enumerate(interactive_elements):
        agent_id = str(i + 1)
        element['agent-id'] = agent_id
        
        text = ' '.join(element.stripped_strings)
        if not text:
            text = element.get('aria-label') or element.get('placeholder') or element.get('name') or ''
        
        simplified_elements.append(f"[{agent_id}] <{element.name}> {text[:100]}")

    return "\n".join(simplified_elements), str(soup)