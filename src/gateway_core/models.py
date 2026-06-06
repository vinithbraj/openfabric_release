from __future__ import annotations

from pydantic import BaseModel, Field


class ExecRequest(BaseModel):
    node: str
    command: str
    execution_id: str | None = None
    env: dict[str, str] | None = None
    stdin: str | None = None


class TerminalExecRequest(BaseModel):
    node: str
    session_id: str
    command: str
    display_command: str | None = None
    execution_id: str | None = None


class TerminalSessionRequest(BaseModel):
    node: str
    session_id: str | None = None
    initial_cwd: str | None = None
    rows: int | None = None
    cols: int | None = None


class TerminalSessionResponse(BaseModel):
    ok: bool
    session_id: str
    cwd: str


class TerminalWriteRequest(BaseModel):
    node: str
    session_id: str
    text: str


class RestartRequest(BaseModel):
    node: str


class RestartResponse(BaseModel):
    status: str
    mode: str
    pid: int | None = None


class LlmRuntimeStartRequest(BaseModel):
    node: str
    command: str
    conda_env: str = "vllm"
    cwd: str | None = None
    restart: bool = False


class LlmRuntimeStopRequest(BaseModel):
    node: str


class LlmRuntimeStatusResponse(BaseModel):
    status: str
    running: bool
    pid: int | None = None
    command: str | None = None
    conda_env: str | None = None
    cwd: str | None = None
    log_path: str | None = None
    started_at: str | None = None
    exit_code: int | None = None
    message: str = ""


class LlmRuntimeLogResponse(BaseModel):
    status: str
    running: bool
    log_path: str | None = None
    offset: int = 0
    next_offset: int = 0
    text: str = ""
    truncated: bool = False
    message: str = ""


class TerminalDetachedExecResponse(BaseModel):
    ok: bool
    session_id: str
    message: str


class ExecCancelRequest(BaseModel):
    node: str
    execution_id: str


class ExecCancelResponse(BaseModel):
    cancelled: bool
    execution_id: str
    message: str


class ExecResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


class HealthResponse(BaseModel):
    status: str
    node: str


class CapabilityInfo(BaseModel):
    name: str
    description: str


class CapabilitiesResponse(BaseModel):
    node: str
    version: str
    platform: str = "unknown"
    platform_label: str = "Unknown"
    platform_version: str = ""
    architecture: str = ""
    shell: str = ""
    command_profile: str = ""
    capability_tags: list[str] = Field(default_factory=list)
    capabilities: list[CapabilityInfo]


class ExecStreamEvent(BaseModel):
    type: str
    text: str = ""
    exit_code: int | None = None


class TerminalClientMessage(BaseModel):
    type: str
    data: str = ""
    rows: int | None = None
    cols: int | None = None
