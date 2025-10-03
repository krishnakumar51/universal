from typing import TypedDict, List, Dict, Any
from pathlib import Path

class AgentState(TypedDict):
    """Defines the structure of the agent's state that is passed between nodes."""
    job_id: str
    query: str
    url: str
    provider: str
    
    # Planning & Task Management
    plan_details: Dict[str, Any]
    current_task: str

    # Browser & Page State
    page_content: str
    modified_html_for_action: str
    
    # Results & Artifacts
    results: List[dict]
    generated_credentials: Dict[str, str]
    screenshots: List[str]
    job_artifacts_dir: Path
    
    # Execution Flow & History
    step: int
    max_steps: int
    history: List[str]
    execution_summary: List[str]
    
    # Self-Healing & Retry Mechanism
    last_action: Dict[str, Any]
    last_action_outcome: str
    retry_count: int
    last_error: str
    research_summary: str

