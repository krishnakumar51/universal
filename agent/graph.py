import json
from urllib.parse import urljoin
from pathlib import Path
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from langchain_core.runnables import RunnableConfig

from agent.state import AgentState
from agent.llm import get_structured_plan, get_agent_action, get_research_analysis, get_updated_plan
from browser.utils import resize_image_if_needed, simplify_page_for_llm
from tavily import TavilyClient
from config.settings import TAVILY_API_KEY
from langgraph.graph import StateGraph

tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None

push_status_update = None

def set_push_status_update(func):
    global push_status_update
    push_status_update = func

def get_page_from_config(config: RunnableConfig) -> Page:
    page = config.get("configurable", {}).get("page")
    if not page:
        raise ValueError("Playwright Page object not found in configuration.")
    return page

def planning_node(state: AgentState, config: RunnableConfig) -> AgentState:
    page = get_page_from_config(config)
    state['url'] = page.url 
    if push_status_update:
        push_status_update(state['job_id'], "planning_started")
    
    plan_details = get_structured_plan(state['query'], state['provider'], state['url'])
    state['plan_details'] = plan_details
    
    if push_status_update:
        push_status_update(state['job_id'], "plan_generated", {"plan": plan_details})
    
    summary = (
        f"[Plan Generated]\n"
        f"Objective: {plan_details.get('objective', 'Not specified')}\n"
        f"Intent: {plan_details.get('intent', 'Not specified')}\n"
        f"Plan:\n" + "\n".join([f"  - {step}" for step in plan_details.get('plan', [])])
    )
    state['execution_summary'].append(summary)
    return state

def agent_reasoning_node(state: AgentState, config: RunnableConfig) -> AgentState:
    page = get_page_from_config(config)
    job_id = state['job_id']
    if push_status_update:
        push_status_update(job_id, "agent_step", {"step": state['step'], "max_steps": state['max_steps']})

    current_plan_step_index = state['step'] - 1
    plan = state['plan_details'].get('plan', [])
    if current_plan_step_index < len(plan):
        state['current_task'] = plan[current_plan_step_index]
    else:
        state['current_task'] = "All plan steps are complete. The final task is to finish the job."

    screenshot_path = state['job_artifacts_dir'] / f"{state['step']:02d}_step.png"
    page.screenshot(path=screenshot_path, full_page=False)
    resize_image_if_needed(screenshot_path)
    
    relative_path = Path("screenshots") / job_id / f"{state['step']:02d}_step.png"
    state['screenshots'].append(relative_path.as_posix())
    
    # NEW: Push status for screenshot to allow dynamic loading in UI
    if push_status_update:
        push_status_update(job_id, "screenshot_taken", {"step": state['step'], "path": relative_path.as_posix()})

    state['page_content'] = page.content()
    state['url'] = page.url
    
    simplified_elements, modified_html = simplify_page_for_llm(state['page_content'])
    if modified_html:
        page.set_content(modified_html)  # FIX: Add this to apply agent-id to live page
    action_response = get_agent_action(state, simplified_elements)
    
    thought = action_response.get("thought", "No thought provided.")
    if push_status_update:
        push_status_update(job_id, "agent_thought", {"thought": thought})
        
    state['last_action'] = action_response.get("action", {})
    state['execution_summary'].append(f"\n[Step {state['step']}] Task: {state['current_task']}\n  -> Thought: {thought}")
    state['modified_html_for_action'] = modified_html
    
    return state
def execute_action_node(state: AgentState, config: RunnableConfig) -> AgentState:
    page = get_page_from_config(config)
    action = state.get('last_action', {})
    job_id = state['job_id']
    
    if push_status_update:
        push_status_update(job_id, "executing_action", {"action": action})
    
    outcome = "Success"
    error_message = ""

    if not action or "type" not in action:
        outcome = "FAILED"
        error_message = "Agent produced an invalid or empty action."
    else:
        try:
            action_type = action.get("type")
            
            if action_type in ["click", "press_enter"]:
                selector = f"[agent-id='{action['id']}']"
                element = page.locator(selector).first
                element.wait_for(state='visible', timeout=30000)  # IMPROVE: Wait for visibility before action
                
                # FIX: Remove expect_navigation; handle dynamically
                if action_type == "click":
                    element.click(timeout=30000)  # Increased timeout
                elif action_type == "press_enter":
                    element.press('Enter', timeout=30000)
                page.wait_for_load_state('domcontentloaded', timeout=30000)  # Wait after action

            elif action_type == "fill":
                selector = f"[agent-id='{action['id']}']"
                element = page.locator(selector).first
                element.wait_for(state='visible', timeout=30000)
                element.fill(action["text"], timeout=30000)
                
            elif action_type == "scroll":
                direction = action.get("direction", "down")
                scroll_amount = "window.innerHeight * 0.8" if direction == "down" else "-window.innerHeight * 0.8"
                page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                page.wait_for_timeout(2000)  # Increased for stability
            
            elif action_type == "wait":
                page.wait_for_timeout(10000)  # Increased to 10s for better loading

            elif action_type == "extract":
                items = action.get("items", [])
                state['results'].extend(items)
                if push_status_update:
                    push_status_update(job_id, "partial_result", {"items": items})
            
            page.wait_for_timeout(5000)  # General post-action wait, increased
            
        except PlaywrightTimeoutError as e:
            error_message = str(e).splitlines()[0]
            # FIX: If timeout was on navigation expectation (removed), treat as success if no nav needed
            outcome = "FAILED"
        except Exception as e:
            error_message = str(e).splitlines()[0]
            outcome = "FAILED"

    if outcome == "FAILED" and push_status_update:
        push_status_update(job_id, "action_failed", {"action": action, "error": error_message})
        
    state['last_action_outcome'] = outcome
    state['last_error'] = error_message
    state['history'].append(f"Step {state['step']} (Task: {state['current_task']}): Action `{json.dumps(action)}` -> {outcome}.")
    state['execution_summary'].append(f"  -> Action: {json.dumps(action)}\n  -> Outcome: {outcome}" + (f" ({error_message})" if error_message else ""))
    
    if outcome == "Success":
        state['step'] += 1  # Advance even after retry success
    
    state['history'] = state['history'][-5:]
    
    return state

def researcher_node(state: AgentState, config: RunnableConfig) -> AgentState:
    if not tavily_client:
        state['research_summary'] = "Tavily API key not configured. Skipping research."
        return state

    query = f"How to achieve this task: '{state['current_task']}' on the website {state['url']}?"  # FIX: Remove "using Playwright and CSS selectors" to avoid mismatch
    
    if push_status_update:
        push_status_update(state['job_id'], "research_started", {"query": query})
    
    try:
        response = tavily_client.search(query=query, search_depth="advanced")
        context = [{"url": obj["url"], "content": obj["content"]} for obj in response.get("results", [])]
        
        analysis = get_research_analysis(state, context, state['provider'])
        state['research_summary'] = analysis
    except Exception as e:
        state['research_summary'] = f"Research failed: {str(e)}"

    if push_status_update:
        push_status_update(state['job_id'], "research_complete", {"summary": state['research_summary']})
    
    return state

def plan_updater_node(state: AgentState, config: RunnableConfig) -> AgentState:
    if push_status_update:
        push_status_update(state['job_id'], "updating_plan")

    new_plan_details = get_updated_plan(state, state['provider'])
    state['plan_details'] = new_plan_details
    
    if push_status_update:
        push_status_update(state['job_id'], "plan_updated", {"plan": new_plan_details})
        
    state['execution_summary'].append(f"\n[Plan Updated after Research]\nNew Plan:\n" + "\n".join([f"  - {step}" for step in new_plan_details.get('plan', [])]))
    return state

def validator_and_router_node(state: AgentState) -> str:
    if state['last_action_outcome'] == "FAILED":
        if state['retry_count'] < 2:
            state['retry_count'] += 1
            return "retry"
        else:
            state['execution_summary'].append("\n[Job Halted] Maximum retries reached for a failing step.")
            return "__end__"

    state['retry_count'] = 0

    # IMPROVE: Add target_count check like in example code
    target_count = state['plan_details'].get('target_count', float('inf'))
    if len(state['results']) >= target_count:
        if push_status_update:
            push_status_update(state['job_id'], "agent_finished", {"reason": f"Collected {len(state['results'])}/{target_count} items."})
        return "__end__"

    if state.get('last_action', {}).get("type") == "finish":
        reason = state['last_action'].get("reason", "Task completed.")
        if push_status_update:
            push_status_update(state['job_id'], "agent_finished", {"reason": reason})
        return "__end__"
    
    if state['step'] > len(state['plan_details'].get('plan', [])):
         if push_status_update:
            push_status_update(state['job_id'], "agent_finished", {"reason": "All plan steps completed."})
         return "__end__"

    if state['step'] > state['max_steps']:
        if push_status_update:
            push_status_update(state['job_id'], "agent_stopped", {"reason": "Max steps reached."})
        return "__end__"
        
    return "continue"

def create_graph() -> StateGraph:
    builder = StateGraph(AgentState)
    builder.add_node("planner", planning_node)
    builder.add_node("agent_reasoner", agent_reasoning_node)
    builder.add_node("action_executor", execute_action_node)
    builder.add_node("researcher", researcher_node)
    builder.add_node("plan_updater", plan_updater_node)
    
    builder.set_entry_point("planner")
    builder.add_edge("planner", "agent_reasoner")
    builder.add_edge("agent_reasoner", "action_executor")
    builder.add_edge("researcher", "plan_updater")
    builder.add_edge("plan_updater", "agent_reasoner")

    builder.add_conditional_edges("action_executor", validator_and_router_node, {
        "continue": "agent_reasoner",
        "retry": "researcher",
        "__end__": "__end__"
    })
    return builder.compile()

if __name__ == "__main__":
    graph = create_graph()
    try:
        mermaid_graph = graph.get_graph().draw_mermaid()
        print("\n--- Agent Workflow (Mermaid Diagram) ---")
        print(mermaid_graph)
        print("----------------------------------------\n")
        with open("agent_workflow.md", "w") as f:
            f.write("```mermaid\n")
            f.write(mermaid_graph)
            f.write("\n```")
        print("✅ Agent workflow diagram saved to `agent_workflow.md`")
    except Exception as e:
        print(f"Could not generate graph visualization: {e}")