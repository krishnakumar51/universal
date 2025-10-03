import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- LLM API Keys ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# --- LLM Model Selection ---
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama3-70b-8192")

# --- Directory Configuration ---
PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
RESULTS_DIR = PROJECT_ROOT / "results"

STATIC_DIR.mkdir(exist_ok=True)
SCREENSHOTS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# --- Browser Configuration ---
VIEWPORT_SIZE = {"width": 1280, "height": 1080}

# --- LLM Client Initialization ---
anthropic_client = None
groq_client = None
openai_client = None

try:
    if ANTHROPIC_API_KEY:
        from anthropic import Anthropic
        anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    else:
        print("Warning: ANTHROPIC_API_KEY not found. Anthropic provider will be unavailable.")
except ImportError:
    print("Warning: 'anthropic' library not installed. Anthropic provider will be unavailable.")

try:
    if GROQ_API_KEY:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
    else:
        print("Warning: GROQ_API_KEY not found. Groq provider will be unavailable.")
except ImportError:
    print("Warning: 'groq' library not installed. Groq provider will be unavailable.")

try:
    if OPENAI_API_KEY:
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    else:
        print("Warning: OPENAI_API_KEY not found. OpenAI provider will be unavailable.")
except ImportError:
    print("Warning: 'openai' library not installed. OpenAI provider will be unavailable.")

