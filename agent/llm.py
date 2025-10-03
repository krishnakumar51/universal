import json
import base64
import re
from pathlib import Path
from typing import List, Dict
import time
from agent.state import AgentState
from agent.prompts import (
    PLANNER_PROMPT, AGENT_PROMPT, RESEARCHER_PROMPT, PLAN_UPDATER_PROMPT
)
from config.settings import (
    anthropic_client, groq_client, openai_client,
    ANTHROPIC_MODEL, GROQ_MODEL, OPENAI_MODEL
)

LLMProvider = str

def get_llm_response(system_prompt: str, prompt: str, provider: LLMProvider, images: List[Path]) -> str:
    for attempt in range(3):  # IMPROVE: Add retries for LLM calls
        try:
            if provider == "anthropic":
                if not anthropic_client: raise ValueError("Anthropic client not initialized.")
                return call_anthropic(system_prompt, prompt, images)
            elif provider == "openai":
                if not openai_client: raise ValueError("OpenAI client not initialized.")
                return call_openai(system_prompt, prompt, images)
            elif provider == "groq":
                if not groq_client: raise ValueError("Groq client not initialized.")
                if images: raise ValueError("The configured Groq model does not support vision.")
                return call_groq(system_prompt, prompt)
            else:
                raise ValueError(f"Unsupported LLM provider: {provider}")
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2)  # Backoff

def call_anthropic(system_prompt: str, prompt: str, images: List[Path]) -> str:
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    for img_path in images:
        with open(img_path, "rb") as f: img_data = base64.b64encode(f.read()).decode("utf-8")
        messages[0]["content"].append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_data}})
    response = anthropic_client.messages.create(model=ANTHROPIC_MODEL, max_tokens=8192, system=system_prompt, messages=messages, timeout=60)  # Add timeout
    return response.content[0].text

def call_openai(system_prompt: str, prompt: str, images: List[Path]) -> str:
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    for img_path in images:
        with open(img_path, "rb") as f: img_data = base64.b64encode(f.read()).decode("utf-8")
        messages[0]["content"].append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_data}"}})
    response = openai_client.chat.completions.create(model=OPENAI_MODEL, max_tokens=8192, messages=[{"role": "system", "content": system_prompt}, *messages], response_format={"type": "json_object"}, timeout=60)
    return response.choices[0].message.content

def call_groq(system_prompt: str, prompt: str) -> str:
    response = groq_client.chat.completions.create(model=GROQ_MODEL, max_tokens=8192, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}], response_format={"type": "json_object"}, timeout=60)
    return response.choices[0].message.content

def extract_json_from_response(text: str) -> Dict:
    start_brace_index = text.find('{')
    if start_brace_index == -1:
        raise ValueError(f"No JSON object found in the model's response: {text}")

    end_brace_index = text.rfind('}')
    if end_brace_index == -1:
        json_str = text[start_brace_index:]
    else:
        json_str = text[start_brace_index : end_brace_index + 1]

    try:
        json_str_fixed = re.sub(r',\s*([\}\]])', r'\1', json_str)
        
        open_braces = json_str_fixed.count('{')
        close_braces = json_str_fixed.count('}')
        if open_braces > close_braces:
            json_str_fixed += '}' * (open_braces - close_braces)

        return json.loads(json_str_fixed)
    except json.JSONDecodeError as e:
        # IMPROVE: Fallback to empty action if invalid
        print(f"Failed to decode JSON: {e}\nOriginal: {json_str}")
        return {"thought": "Invalid response from LLM", "action": {"type": "wait"}}  # Fallback to prevent crash
        
def get_structured_plan(query: str, provider: LLMProvider, url: str) -> dict:
    prompt = PLANNER_PROMPT.format(query=query, url=url)
    system_prompt = "You are an expert planner. Respond ONLY with the JSON plan."
    response_text = get_llm_response(system_prompt, prompt, provider, images=[])
    return extract_json_from_response(response_text)

def get_agent_action(state: AgentState, simplified_elements: str) -> dict:
    prompt = AGENT_PROMPT.format(
        query=state['query'],
        plan="\n".join([f"- {step}" for step in state['plan_details'].get('plan', [])]),
        current_task=state['current_task'],
        url=state['url'],
        history="\n".join(state['history']) or "No actions taken yet.",
        elements=simplified_elements or "No interactive elements found on the page."
    )
    system_prompt = "You are a web agent. Respond ONLY with your JSON thought and action."
    screenshot_path = state['job_artifacts_dir'] / f"{state['step']:02d}_step.png"
    response_text = get_llm_response(system_prompt, prompt, state['provider'], images=[screenshot_path])
    return extract_json_from_response(response_text)

def get_research_analysis(state: AgentState, context: list, provider: LLMProvider) -> str:
    prompt = RESEARCHER_PROMPT.format(
        query=state['query'],
        current_task=state['current_task'],
        error=state['last_error'],
        context=json.dumps(context, indent=2)
    )
    system_prompt = "You are a research analyst. Analyze the provided context and suggest a concise, actionable solution. Respond with your analysis as a plain string."
    response_text = get_llm_response(system_prompt, prompt, provider, images=[])
    return response_text

def get_updated_plan(state: AgentState, provider: LLMProvider) -> dict:
    prompt = PLAN_UPDATER_PROMPT.format(
        plan=json.dumps(state['plan_details'], indent=2),
        current_task=state['current_task'],
        error=state['last_error'],
        research_summary=state['research_summary']
    )
    system_prompt = "You are a planner. Update the provided JSON plan based on the research summary. Respond ONLY with the updated, valid JSON plan."
    response_text = get_llm_response(system_prompt, prompt, provider, images=[])
    return extract_json_from_response(response_text)