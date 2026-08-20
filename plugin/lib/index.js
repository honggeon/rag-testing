/**
 * dsh-rag-testing — Host 半。
 *
 * 职责（架构 v0.3 §5.1 / ui-design §9）：
 * - 挂 /plugins/rag-testing/api/* 十条路由（ctx.webServer.register 原始路由）
 * - 只读 artifacts 目录（suites 扫描 / runs 列表 / run.json / status.json / raw）
 * - 两个写动作：spawn `ragtest run`（POST /runs）、spawn `ragtest baseline-update`（POST /baselines/update）
 * - 取消：对进程组发 SIGTERM（引擎迁移 CANCELLED → CLEANUP）
 *
 * 安全：密码只从 host 进程环境变量 RAGTEST_ARAG_ADMIN_PASSWORD 读，不落盘、不进响应。
 * 偏离说明：架构原计划用 ctx.subprocess，实现用 node:child_process（宿主进程内等价，零依赖）。
 */

import { spawn } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const inject = ['webServer'];

const API_PREFIX = '/plugins/rag-testing/api';

export function apply(ctx, config = {}) {
  const pluginDir = dirname(realpathSync(fileURLToPath(import.meta.url))); // lib/
  const engineDir = config.engineDir
    ? resolve(config.engineDir)
    : resolve(pluginDir, '..', '..', 'engine');
  const repoRoot = resolve(engineDir, '..');
  const artifactsDir = join(engineDir, 'artifacts');
  const suitesDir = join(repoRoot, 'suites', 'golden');

  /** 运行中的子进程：run_id → ChildProcess */
  const running = new Map();

  // ── 工具 ────────────────────────────────────────────────────────────────

  function json(res, obj, status = 200) {
    const body = JSON.stringify(obj);
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(body);
  }

  function readJsonFile(path) {
    try {
      return JSON.parse(readFileSync(path, 'utf-8'));
    } catch {
      return null;
    }
  }

  async function readBody(req) {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    try {
      return JSON.parse(Buffer.concat(chunks).toString('utf-8') || '{}');
    } catch {
      return {};
    }
  }

  /** 扫描 suites/golden/*.yaml，regex 提取轻量元数据（host 无 yaml 依赖）。 */
  function listSuites() {
    if (!existsSync(suitesDir)) return [];
    return readdirSync(suitesDir)
      .filter((f) => f.endsWith('.yaml') || f.endsWith('.yml'))
      .map((f) => {
        const text = readFileSync(join(suitesDir, f), 'utf-8');
        const pick = (re) => (text.match(re) || [])[1] || '';
        const caseCount = (text.match(/^\s*- id: /gm) || []).length;
        const tagsMatch = text.match(/^tags:\s*\[(.*)\]/m);
        return {
          id: pick(/^id:\s*(.+)$/m),
          name: pick(/^name:\s*(.+)$/m),
          file: f,
          tags: tagsMatch ? tagsMatch[1].split(',').map((s) => s.trim()) : [],
          case_count: caseCount,
          defect: /(^|\s)defect/.test(tagsMatch ? tagsMatch[1] : ''),
        };
      })
      .filter((s) => s.id);
  }

  /** 扫描 artifacts/runs：status.json + run.json summary 合并。 */
  function listRuns() {
    const runsDir = join(artifactsDir, 'runs');
    if (!existsSync(runsDir)) return [];
    return readdirSync(runsDir)
      .map((runId) => {
        const dir = join(runsDir, runId);
        const status = readJsonFile(join(dir, 'status.json'));
        const runJson = readJsonFile(join(dir, 'run.json'));
        return {
          run_id: runId,
          state: status?.state ?? 'UNKNOWN',
          progress: status?.progress ?? null,
          heartbeat_at: status?.heartbeat_at ?? null,
          suite_id: runJson?.suite?.id ?? null,
          suite_name: runJson?.suite?.name ?? null,
          summary: runJson?.summary ?? null,
          gate: runJson?.gate ?? null,
          defect: (runJson?.suite?.tags || []).includes?.('defect') ?? false,
          started_at: status?.started_at ?? null,
        };
      })
      .sort((a, b) => (b.run_id < a.run_id ? -1 : 1));
  }

  /** spawn ragtest CLI。env：config 覆盖 + 密码来自 host 进程环境。 */
  function spawnEngine(runId, argv) {
    const env = {
      ...process.env,
      RAGTEST_ARAG_BASE_URL: config.aragBaseUrl || '',
      RAGTEST_ARAG_AUTH_URL: config.aragAuthUrl || '',
      RAGTEST_ARAG_ADMIN_EMAIL: config.aragAdminEmail || 'ops@internal',
      RAGTEST_ARAG_ADMIN_PASSWORD: process.env.RAGTEST_ARAG_ADMIN_PASSWORD || '',
      RAGTEST_AGENT_BASE_URL: config.agentBaseUrl || '',
    };
    const child = spawn('uv', ['run', 'python', '-m', 'ragtest.cli', ...argv], {
      cwd: engineDir,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    running.set(runId, child);
    let stderrTail = '';
    child.stderr.on('data', (d) => {
      stderrTail = (stderrTail + d.toString()).slice(-4000);
    });
    child.on('exit', (code) => {
      running.delete(runId);
      ctx.logger?.info?.(`ragtest run ${runId} exited with ${code}`);
      if (code !== 0) ctx.logger?.warn?.(`ragtest ${runId} stderr tail: ${stderrTail}`);
    });
    return child;
  }

  // ── 路由 ────────────────────────────────────────────────────────────────

  async function handler(req, res) {
    const url = new URL(req.url, 'http://localhost');
    const sub = url.pathname.slice(API_PREFIX.length).replace(/^\/+|\/+$/g, '');
    const parts = sub.split('/').filter(Boolean);

    try {
      // GET /suites
      if (req.method === 'GET' && sub === 'suites') {
        return json(res, { engine_dir: engineDir, suites: listSuites() });
      }
      // GET /suites/:file/yaml
      if (req.method === 'GET' && parts[0] === 'suites' && parts[2] === 'yaml') {
        const file = join(suitesDir, parts[1]);
        if (!existsSync(file) || !parts[1].endsWith('.yaml')) return json(res, { error: 'not found' }, 404);
        res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end(readFileSync(file, 'utf-8'));
      }
      // GET /runs
      if (req.method === 'GET' && sub === 'runs') {
        return json(res, { runs: listRuns() });
      }
      // POST /runs {suite_file} —— 触发运行
      if (req.method === 'POST' && sub === 'runs') {
        const body = await readBody(req);
        const suiteFile = body.suite_file;
        if (!suiteFile || !existsSync(join(suitesDir, suiteFile))) {
          return json(res, { error: `suite 不存在: ${suiteFile}` }, 404);
        }
        if (running.size > 0) {
          return json(res, { error: '已有运行进行中（单并发）' }, 409);
        }
        if (!process.env.RAGTEST_ARAG_ADMIN_PASSWORD) {
          return json(res, { error: 'host 进程缺少 RAGTEST_ARAG_ADMIN_PASSWORD 环境变量' }, 503);
        }
        const runId = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
          .replace(/^(\d{8})(\d{6})$/, '$1-$2')
          + '-' + Math.random().toString(16).slice(2, 8);
        const argv = ['run', '--suite', join(suitesDir, suiteFile), '--run-id', runId];
        if (body.baseline) argv.push('--baseline', body.baseline);
        spawnEngine(runId, argv);
        return json(res, { run_id: runId }, 202);
      }
      // GET /runs/:id/status
      if (req.method === 'GET' && parts[0] === 'runs' && parts[2] === 'status') {
        const status = readJsonFile(join(artifactsDir, 'runs', parts[1], 'status.json'));
        if (!status) return json(res, { error: 'not found' }, 404);
        status.running = running.has(parts[1]);
        return json(res, status);
      }
      // POST /runs/:id/cancel —— SIGTERM（引擎 CANCELLED → CLEANUP）
      if (req.method === 'POST' && parts[0] === 'runs' && parts[2] === 'cancel') {
        const child = running.get(parts[1]);
        if (!child) return json(res, { error: '运行不存在或已结束' }, 404);
        child.kill('SIGTERM');
        return json(res, { ok: true });
      }
      // GET /runs/:id —— run.json 全量
      if (req.method === 'GET' && parts[0] === 'runs' && parts.length === 2) {
        const run = readJsonFile(join(artifactsDir, 'runs', parts[1], 'run.json'));
        if (!run) return json(res, { error: 'not found' }, 404);
        return json(res, run);
      }
      // GET /runs/:id/raw/:file
      if (req.method === 'GET' && parts[0] === 'runs' && parts[2] === 'raw') {
        const file = join(artifactsDir, 'runs', parts[1], 'raw', parts[3] || '');
        if (!existsSync(file)) return json(res, { error: 'not found' }, 404);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        return res.end(readFileSync(file, 'utf-8'));
      }
      // GET /baselines?suite=<id>
      if (req.method === 'GET' && sub === 'baselines') {
        const suiteId = url.searchParams.get('suite');
        const dir = join(artifactsDir, 'baselines', suiteId || '');
        const items = existsSync(dir)
          ? readdirSync(dir).filter((f) => f.endsWith('.json'))
              .map((f) => readJsonFile(join(dir, f))).filter(Boolean)
          : [];
        return json(res, { baselines: items.map((b) => ({
          name: b.name, created_at: b.created_at, run_id: b.run_id, summary: b.summary,
        })) });
      }
      // POST /baselines/update {suite_file, name, run_dir}
      if (req.method === 'POST' && sub === 'baselines/update') {
        const body = await readBody(req);
        const runDir = join(artifactsDir, 'runs', body.run_id || '');
        if (!existsSync(join(runDir, 'run.json'))) return json(res, { error: 'run 不存在' }, 404);
        const runId = `baseline-${Date.now()}`;
        spawnEngine(runId, [
          'baseline-update', '--suite', join(suitesDir, body.suite_file),
          '--name', body.name || 'main', '--run', runDir,
        ]);
        return json(res, { ok: true }, 202);
      }
      return json(res, { error: `unknown route: ${req.method} ${sub}` }, 404);
    } catch (e) {
      ctx.logger?.error?.('rag-testing api error', e);
      return json(res, { error: String(e?.message || e) }, 500);
    }
  }

  ctx.webServer.register({ kind: 'prefix', path: API_PREFIX, handler });
  ctx.logger?.info?.(`dsh-rag-testing host ready（engine: ${engineDir}）`);
}
