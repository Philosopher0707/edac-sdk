"""Server configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseSettings):
    """Production server configuration."""

    model_config = SettingsConfigDict(
        env_prefix="EDAC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    reload: bool = False
    log_level: str = "info"

    # Database
    database_url: str = "sqlite+aiosqlite:///./edac.db"
    db_pool_size: int = 5
    chat_db_path: str = "data/chat.db"

    # Security
    api_key: Optional[str] = None
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])

    # Model Providers
    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "kimi-k2.6:cloud"
    anthropic_api_key: Optional[str] = None
    anthropic_default_model: str = "claude-sonnet-4-6"
    openai_api_key: Optional[str] = None
    openai_default_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"

    # Agent Defaults
    default_max_restarts: int = 3
    default_heartbeat_interval: float = 5.0
    default_heartbeat_timeout: float = 15.0
    default_max_parallel: int = 3
    default_sandbox: bool = False

    # Context
    max_tokens_per_agent: int = 128000
    max_tokens_per_session: int = 512000
    cheap_model_threshold: int = 4000

    # Task Queue
    task_queue_maxsize: int = 1000
    task_timeout: float = 300.0
    worker_count: int = 2
    queue_backend: str = "asyncio"  # asyncio | redis

    # Resilience
    retry_max_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_recovery: float = 30.0
    rate_limit_capacity: float = 100.0
    rate_limit_refill: float = 10.0

    # Observability
    metrics_enabled: bool = True
    tracing_enabled: bool = True
    metrics_port: int = 9090
    audit_logging: bool = True

    # Paths
    data_dir: Path = Path("./data")
    skills_dir: Path = Path("./skills")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
