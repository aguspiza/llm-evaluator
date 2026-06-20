# LLM Evaluator

Automated benchmarking tool for evaluating LLM models served by local/remote `llama.cpp` instances (and any OpenAI-compatible endpoint such as OpenRouter). Each response is scored 0–10 by a configurable **judge**, and synthetic prompt-processing / token-generation throughput is measured via llama.cpp's benchmark endpoint.

## Architecture

Target models communicate through a unified OpenAI-compatible client:
- **Local**: `llama-server` started on a local port.
- **Remote**: `llama-server` started on another machine over SSH — either via the `llamaremote` wrapper or by pointing `exe` at a `llama-server` binary on the remote host.
- **OpenRouter** (or any OpenAI-compatible API): direct HTTP calls, no server managed.

A run happens in **two phases** so model and judge servers never need to be resident at the same time:
1. **Collect** — start each unique model server, run every test, save responses, then stop all model servers to free RAM/VRAM.
2. **Judge** — start the judge, score every collected response.

The judge is independent of the target models and is selected with `--judge` (see [Judges](#judges)).

## Project Structure

```
llm-evaluator/
├── main.py                      # CLI entry point (typer)
├── config.yaml                  # Models, judge(s), system prompt reference
├── tests.yaml                   # Test + benchmark definitions
├── requirements.txt
├── llamaremote                  # SSH wrapper for remote llama-server
├── test_judges.py               # Smoke test for the 3 judge providers
├── prompts/
│   ├── system.txt               # Global system prompt
│   ├── tests/                   # Test prompts (one per file)
│   └── evaluations/             # Evaluation criteria (one per test)
├── results/                     # Run output JSON (created automatically)
└── llm_evaluator/
    ├── config_loader.py         # YAML loading + ${ENV_VAR} resolution
    ├── client.py                # Unified OpenAI-compatible client
    ├── server_manager.py        # Start/stop local & remote llama-server
    ├── runner.py                # Two-phase collect + judge
    ├── evaluator.py             # Judge: score 0-10 + justification
    └── reporter.py              # Console tables + JSON export
```

## Setup

Dependencies are listed in `requirements.txt`; the project is run with [`uv`](https://docs.astral.sh/uv/):

```bash
uv run python main.py run ...
```

Environment / tooling needed depends on which judge and models you use:
- **OpenRouter** models or judge: `export OPENROUTER_API_KEY="sk-or-..."` (referenced in `config.yaml` as `${OPENROUTER_API_KEY}`).
- **claude-code** judge: the `claude` CLI must be installed and on `PATH` (no API key — it shells out to the CLI).
- **anthropic** judge: `export ANTHROPIC_API_KEY="sk-ant-..."`.

## Configuration

### `config.yaml`

```yaml
system_prompt: "./prompts/system.txt"

models:
  - name: "Gemma4-E4B (Local)"
    type: local
    hf_repo: "unsloth/gemma-4-E4B-it-GGUF:Q4_0"
    port: 8082

  - name: "LFM2.5-8B-A1B Q4_K_M (Remote GTX1060 ncmoe12)"
    type: remote
    host: "admin@pc1060.intra"
    hf_repo: "LiquidAI/LFM2.5-8B-A1B-GGUF:Q4_K_M"
    port: 8082
    exe: "~/Downloads/llama-b8679-bin-win-cuda-12.4-x64/llama-server.exe"
    ctk: "q4_0"
    ctv: "q4_0"
    ctx_size: 65536
    extra_args: ["-ngl", "99", "--n-cpu-moe", "12"]
```

**Model types:**
- `local`: starts `llama-server` locally. Requires `hf_repo` and `port`.
- `remote`: starts `llama-server` via SSH. Requires `host`, `hf_repo`, `port`; set `exe` to run a specific binary on the remote host (WSL/CUDA builds supported), otherwise the `llamaremote` wrapper is used.
- `openrouter`: OpenAI-compatible API. Requires `model` and `api_key`.

**Common per-model options:** `ctx_size`, `ctk`/`ctv` (KV cache quant, default `q4_0`), `no_warmup`, `max_tokens`, `temperature`, and `extra_args` (passed verbatim to `llama-server`, e.g. `-ngl`, `--n-cpu-moe`, `--jinja`, `-b`/`-ub`).

### Judges

A run uses one judge. Without `--judge`, the top-level `judge:` block is used. Pass `--judge <name>` to pick a preset from the `judges:` map:

```yaml
judge:                      # default judge
  name: "Juez"
  type: local
  hf_repo: "unsloth/gemma-4-E4B-it-GGUF:Q4_0"
  port: 8081
  temperature: 0.1

judges:
  local:                    # llama.cpp judge (local or remote)
    type: local
    hf_repo: "unsloth/gemma-4-E4B-it-GGUF:Q4_0"
    port: 8081
    temperature: 0.1

  claude-code:              # shells out to the `claude` CLI (no API key)
    provider: claude-code
    model: "claude-sonnet-4-6"

  openrouter:               # any OpenAI-compatible API
    provider: openrouter
    model: "@preset/good-free"
    base_url: "https://openrouter.ai/api/v1"
    api_key: "${OPENROUTER_API_KEY}"
```

Supported judge providers: `local`/`remote` (llama.cpp), `openrouter` (OpenAI-compatible), `claude-code` (CLI), and `anthropic` (Anthropic SDK). A llama.cpp judge that shares its `hf_repo` with a just-run model reuses that server instead of starting a second one.

> `test_judges.py` runs a one-shot sanity check of the `local`, `claude-code`, and `openrouter` presets:
> `uv run python test_judges.py [local|claude-code|openrouter]`.

### `tests.yaml`

Two kinds of entries — quality tests and synthetic benchmarks:

```yaml
tests:
  - id: logic_01                         # quality test
    category: razonamiento_logico
    prompt_file: "./prompts/tests/logic_01.txt"
    evaluation_file: "./prompts/evaluations/logic_01.txt"

  - id: bench_pp512                       # benchmark (no judging)
    category: benchmark
    benchmark: true
    n_prompt: 512                         # prompt tokens to process
    n_generate: 0                         # tokens to generate
```

A quality test references a prompt file (sent to the model) and an evaluation file (criteria the judge scores against). A benchmark entry measures prompt-processing (PP) and token-generation (TG) throughput and is not judged.

### Prompts

```
prompts/
├── system.txt          # "Eres un asistente util y preciso..."
├── tests/
│   └── logic_01.txt    # "Si todos los A son B y algunos B son C..."
└── evaluations/
    └── logic_01.txt    # "Evaluar si el modelo identifica correctamente..."
```

## Usage

```bash
# Run all tests against all models (default judge)
uv run python main.py run

# Pick a judge preset
uv run python main.py run --judge claude-code

# Run a single test / single model
uv run python main.py run --test instruction_01
uv run python main.py run --model "Gemma4-E4B (Local)"

# Detailed console output, custom output file
uv run python main.py run --details -o my-run.json

# List configured models and tests
uv run python main.py list-models
uv run python main.py list-tests
```

### CLI Options (`run`)

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to config.yaml |
| `--tests` | `-t` | Path to tests.yaml |
| `--output` | `-o` | Output JSON file (default: `results.json`). Bare filenames are stored under `results/`; pass a path with a directory to override. |
| `--details` | `-d` | Show full responses in console |
| `--model` | `-m` | Run only on this model (by name) |
| `--test` | | Run only this test (by ID) |
| `--judge` | `-j` | Judge preset from the `judges:` map (e.g. `claude-code`, `openrouter`, `local`) |

## Output

### Console

A scores table, a per-model average table, and (when benchmarks ran) a throughput table:

```
LLM Evaluation Results
+--------------------------------------------------+
| Model              | Test       | Score | Time  |
|--------------------+------------+-------+-------|
| gemma4-E4B (Local) | logic_01   |    10 | 186s  |
+--------------------------------------------------+

Benchmark Results (synthetic)
+------------------------------------------------------+
| Model              | Test       | PP tk/s | TG tk/s  |
|--------------------+------------+---------+----------|
| gemma4-E4B (Local) | bench_pp512|   522.2 |    -     |
+------------------------------------------------------+
```

### JSON (`results/`)

Full results — raw prompts, responses, judge scores + justifications, and timing/throughput data — written into `results/`. Saved incrementally after each test so nothing is lost on a crash.

## Server Management

The evaluator automatically:
1. Starts one `llama-server` per unique `hf_repo` (local or remote).
2. Waits for the `/health` endpoint before running tests.
3. Stops all model servers after the collect phase, then starts the judge.
4. Stops everything when done (even on error).

Local server command (KV-cache quant defaults to `q4_0`; `ctx_size`/`no_warmup`/`extra_args` appended when set):
```
llama-server -hf <repo>:<quant> --host 127.0.0.1 --port <port> -ctk q4_0 -ctv q4_0 --no-mmproj
```

Remote with an explicit binary:
```
ssh user@host "<exe> -hf <repo> --host 0.0.0.0 --port <port> -ctk q4_0 -ctv q4_0 <extra_args>"
```

## Results

Quality scores (0–10) graded by the judge model. Throughput measured by llama.cpp's built-in benchmark. Each entry uses the most recent run for that model.

### Quality

| Model | Date | Avg Score | Avg Time |
|---|---|---|---|
| Ternary-Bonsai-8B (Local) | 2026-04-23 | — (all timeout) | — |
| Granite-4.1-8B (Local) | 2026-05-09 | 9.57 | 60.9s |
| Granite-4.1-8B (Remote GTX1060) | 2026-05-10 | 8.43 | 53.9s |
| Gemma4-E4B (Remote GTX1060) | 2026-05-11 | 9.71 | 39.9s |
| Qwen3.6-35B-A3B-IQ2_M (Remote) | 2026-05-18 | 9.9 | 59.0s |
| LFM2.5-8B-A1B Q4_K_M (Remote GTX1060, ncmoe12) | 2026-06-20 | 8.3 | 14.0s |

### Throughput

| Model | PP 512 (tk/s) | PP 16K (tk/s) | TG short (tk/s) | TG 16K (tk/s) |
|---|---|---|---|---|
| Ternary-Bonsai-8B (Local) | — | — | — | — |
| Granite-4.1-8B (Local) | 21.0 | — | 6.8 | — |
| Granite-4.1-8B (Remote GTX1060) | 154.2 | 97.4 | 13.4 | 2.5 |
| Gemma4-E4B (Remote GTX1060) | 522.2 | 201.6 | 1.1 | 17.2 |
| Qwen3.6-35B-A3B-IQ2_M (Remote) | 257.0 | 304.8 | 21.5 | 17.7 |
| LFM2.5-8B-A1B Q4_K_M (Remote GTX1060, ncmoe12) | 780.4 | 719.4 | 45.8 | 41.5 |

> Remote models run on a secondary machine (`pc1060.intra`) with an NVIDIA GTX 1060 6GB.

---

## Adding New Tests

1. Create prompt file: `prompts/tests/my_test.txt`
2. Create evaluation criteria: `prompts/evaluations/my_test.txt`
3. Add entry to `tests.yaml`:
```yaml
  - id: my_test
    category: new_category
    prompt_file: "./prompts/tests/my_test.txt"
    evaluation_file: "./prompts/evaluations/my_test.txt"
```
