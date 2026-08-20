"""Golden Set / Dataset 的 YAML Schema（架构 v0.3 §7，Golden Set v1）。

pydantic 模型同时承担：YAML 加载校验（fail fast）与 run.json 序列化。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1"


# ── Dataset ───────────────────────────────────────────────────────────────


class DatasetDocument(BaseModel):
    logical_id: str
    path: str                                   # 相对 dataset.yaml 所在目录
    sha256: str | None = None                   # 加载时校验/回填
    metadata: dict[str, Any] = Field(default_factory=dict)


class Dataset(BaseModel):
    schema_version: str = SCHEMA_VERSION
    id: str
    version: str
    documents: list[DatasetDocument]


# ── Suite ─────────────────────────────────────────────────────────────────


class DatasetRef(BaseModel):
    ref: str                                    # 相对 suite 文件的路径


class AdaptersSpec(BaseModel):
    provisioning: str = "arag"
    target: str = "arag"


class KBSpecYaml(BaseModel):
    name_prefix: str = "ragtest"
    kb_type: str = "personal"


class IdentitySpec(BaseModel):
    role: str = "user"
    create: bool = False                        # 动态创建测试用户
    no_grant: bool = False                      # 不授权（越权场景）
    grant_level: str | None = "read"            # 授权级别（no_grant 时忽略）


class FingerprintSpec(BaseModel):
    """环境指纹（评审 P1-1）：缺项 → baseline incomparable。"""

    retrieval: dict[str, Any] = Field(default_factory=dict)
    generation: dict[str, Any] = Field(default_factory=dict)


class InputSpec(BaseModel):
    query: str | None = None
    top_k: int = 5
    score_threshold: float = 0.0
    # generation（M4）
    question: str | None = None


class ExpectedSpec(BaseModel):
    documents: list[str] = Field(default_factory=list)          # logical_id
    chunks: list[str] = Field(default_factory=list)
    forbidden_documents: list[str] = Field(default_factory=list)
    golden_facts: list[str] = Field(default_factory=list)
    forbidden_facts: list[str] = Field(default_factory=list)
    answer: dict[str, Any] | None = None


class ExpectErrorSpec(BaseModel):
    kind: str                                   # ErrorKind.value，如 validation


class EvaluatorSpec(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def flatten_params(cls, data: Any) -> Any:
        """YAML 写法 `- {name: recall_at_k, k: 5, threshold: 1.0}`：
        name 之外的键全部收进 params。"""
        if isinstance(data, dict) and "name" in data:
            data = dict(data)
            name = data.pop("name")
            params = dict(data.pop("params", {}) or {})
            params.update(data)
            return {"name": name, "params": params}
        return data


class GoldenCase(BaseModel):
    id: str
    name: str = ""
    tags: list[str] = Field(default_factory=list)
    severity: Literal["critical", "major", "minor"] = "major"
    identity: str = "owner"
    timeout_s: int | None = None
    input: InputSpec
    expected: ExpectedSpec | None = None
    expect_error: ExpectErrorSpec | None = None
    expected_fail: dict[str, Any] | None = None  # defect suite 标记（P1-3）
    evaluators: list[EvaluatorSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def expected_xor_error(self) -> GoldenCase:
        if self.expect_error and self.expected:
            raise ValueError(f"case {self.id}: expect_error 与 expected 互斥")
        if not self.expect_error and self.expected is None:
            raise ValueError(f"case {self.id}: 必须提供 expected 或 expect_error")
        return self


class GoldenSuite(BaseModel):
    schema_version: str = SCHEMA_VERSION
    kind: Literal["GoldenSuite"] = "GoldenSuite"
    id: str
    name: str = ""
    tags: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)
    dataset: DatasetRef
    adapters: AdaptersSpec = Field(default_factory=AdaptersSpec)
    knowledge_base: KBSpecYaml = Field(default_factory=KBSpecYaml)
    identities: dict[str, IdentitySpec] = Field(default_factory=dict)
    identity_binding: dict[str, Any] = Field(default_factory=dict)
    environment_fingerprint: FingerprintSpec = Field(default_factory=FingerprintSpec)
    quality_gate: dict[str, Any] = Field(default_factory=dict)
    cases: list[GoldenCase]

    @property
    def is_defect_suite(self) -> bool:
        return "defect" in self.tags
