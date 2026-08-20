"""引擎配置：环境变量驱动（前缀 RAGTEST_），对应架构 v0.3 §6 的配置约定。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """引擎全局配置。字段名映射环境变量：RAGTEST_<FIELD_NAME>（大写）。"""

    model_config = SettingsConfigDict(env_prefix="RAGTEST_", extra="ignore")

    # ── arag（目标 A）──
    arag_base_url: str = "http://127.0.0.1:9013"
    arag_auth_url: str = "http://127.0.0.1:9011"
    arag_admin_email: str = "admin"
    arag_admin_password: str = ""
    arag_ops_email: str = "ops@internal"
    arag_ops_password: str = ""

    # ── xuanjian-agent（目标 B，M4 起；M0 仅 spike 使用）──
    agent_base_url: str = "http://127.0.0.1:8788"
    agent_uid: str = ""

    # ── LLM Judge（M6，OpenAI 兼容，可插拔）──
    judge_base_url: str = ""
    judge_model: str = ""
    judge_api_key: str = ""
    judge_timeout_s: float = 30.0

    # ── artifacts 目录契约（架构 §5.1）──
    artifacts_dir: Path = Path("artifacts")


def load_settings() -> Settings:
    return Settings()
