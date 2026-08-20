# CI 接入指南

> 核心契约：`ragtest run` 的 **exit code** 是唯一门禁接口——
> 0=通过；1=Quality Gate 失败；2=运行错误；3=配置/资产错误。
> 任何 CI 只需要执行命令并检查 exit code。

## 被测环境要求

- CI runner 必须能访问被测系统（内网地址）：
  - arag：`RAGTEST_ARAG_BASE_URL`（:9013 或 nginx :9360）、`RAGTEST_ARAG_AUTH_URL`（:9011）
  - agent（M4 起）：`RAGTEST_AGENT_BASE_URL`（:8788）
- runner 安装 [uv](https://docs.astral.sh/uv/)（首次 `uv sync` 自动拉 Python 与依赖）

## GitHub Actions

见 [`github-actions.yml`](./github-actions.yml)。要点：**必须自托管 runner**
（`runs-on: [self-hosted, rag-env]`），GitHub 云端 runner 无法访问内网被测环境。

## Jenkins（Declarative Pipeline 片段）

```groovy
stage('RAG Regression') {
  environment {
    RAGTEST_ARAG_BASE_URL = 'http://183.162.245.47:9013'
    RAGTEST_ARAG_AUTH_URL = 'http://183.162.245.47:9011'
    RAGTEST_ARAG_ADMIN_EMAIL = 'ops@internal'
    RAGTEST_ARAG_ADMIN_PASSWORD = credentials('ragtest-arag-admin-password')
  }
  steps {
    dir('engine') {
      sh 'uv run python -m ragtest.cli run --suite ../suites/golden/basic_retrieval.v1.yaml --baseline main'
    }
  }
  post {
    always {
      junit 'engine/artifacts/runs/*/junit.xml'
      archiveArtifacts artifacts: 'engine/artifacts/runs/*/run.json,engine/artifacts/runs/*/summary.md',
                       allowEmptyArchive: true
    }
  }
}
```

## GitLab CI（片段）

```yaml
rag-regression:
  tags: [rag-env]          # 内网 runner
  script:
    - cd engine
    - uv run python -m ragtest.cli run --suite ../suites/golden/basic_retrieval.v1.yaml --baseline main
  artifacts:
    when: always
    reports:
      junit: engine/artifacts/runs/*/junit.xml
    paths:
      - engine/artifacts/runs/
    expire_in: 30 days
```

## Baseline 策略

- `artifacts/baselines/` 随 git 提交（PR 可 review diff）
- feature 分支/PR：`run --baseline main`（只对比，不更新）
- main 分支：测试通过后 `baseline-update` 并回提（见 GitHub Actions 示例末段）
- 环境指纹变更（换 embedding 模型/开关 rerank/换 LLM）后：**重建 baseline**，
  否则 diff 会判 incomparable（这是设计行为，不是故障）
