# LLM Evaluator

Automated benchmarking tool for evaluating LLM models from OpenRouter (free tier) and local/remote llama.cpp instances. Uses a judge model to score responses with configurable criteria.

## Architecture

All models communicate through a unified OpenAI-compatible API:
- **OpenRouter**: Direct API calls to `https://openrouter.ai/api/v1`
- **Local**: `llama-server` running locally on a specified port
- **Remote**: `llama-server` on another machine via SSH (using `llamaremote` wrapper)

The judge model reuses the same server if it shares the `hf_repo` with a target model, avoiding duplicate memory usage.

## Project Structure

```
llm-evaluator/
├── main.py                      # CLI entry point
├── config.yaml                  # Models, judge, system prompt reference
├── tests.yaml                   # Test definitions with file references
├── requirements.txt
├── llamaremote                  # SSH wrapper for remote llama-server
├── prompts/
│   ├── system.txt               # Global system prompt
│   ├── tests/                   # Test prompts (one per file)
│   └── evaluations/             # Evaluation criteria (one per test)
└── llm_evaluator/
    ├── config_loader.py         # YAML loading + ${ENV_VAR} resolution
    ├── client.py                # Unified OpenAI-compatible client
    ├── server_manager.py        # Start/stop llama-server instances
    ├── runner.py                # Execute tests × models
    ├── evaluator.py             # Judge: score 0-10 + justification
    └── reporter.py              # Console table + JSON export
```

## Setup

```bash
pip install -r requirements.txt
export OPENROUTER_API_KEY="sk-or-..."
```

## Configuration

### `config.yaml`

```yaml
system_prompt: "./prompts/system.txt"

models:
  - name: "Llama 3.1 8B (Free)"
    type: openrouter
    model: "meta-llama/llama-3.1-8b-instruct:free"
    api_key: "${OPENROUTER_API_KEY}"

  - name: "Gemma 4 E4B (Local)"
    type: local
    hf_repo: "unsloth/gemma-4-E4B-it-GGUF:Q4_0"
    port: 8082

  - name: "Qwen 2.5 (Remote)"
    type: remote
    host: "user@pc1060.intra"
    hf_repo: "Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_0"
    port: 8080

judge:
  name: "Juez"
  type: local
  hf_repo: "unsloth/gemma-4-E4B-it-GGUF:Q4_0"
  port: 8083
  temperature: 0.1
```

**Model types:**
- `openrouter`: Uses OpenRouter API. Requires `model` and `api_key`.
- `local`: Starts `llama-server` locally. Requires `hf_repo` and `port`.
- `remote`: Starts `llama-server` via SSH. Requires `host`, `hf_repo`, and `port`.

**Judge reusing:** If the judge has the same `hf_repo` as a target model, it reuses the same server instance instead of starting a second one.

### `tests.yaml`

```yaml
tests:
  - id: logic_01
    category: razonamiento_logico
    prompt_file: "./prompts/tests/logic_01.txt"
    evaluation_file: "./prompts/evaluations/logic_01.txt"
```

Each test references two files: the prompt to send to the model and the criteria the judge uses to evaluate the response.

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
# Run all tests against all models
python main.py run

# Run a single test
python main.py run --test instruction_01

# Run on a specific model
python main.py run --model "Gemma 4 E4B (Local)"

# Run with detailed output
python main.py run --details

# Filter by model and test, save to custom file
python main.py run --model "Gemma 4 E4B (Local)" --test code_01 -o results_code.json

# List configured models and tests
python main.py list-models
python main.py list-tests
```

### CLI Options

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to config.yaml |
| `--tests` | `-t` | Path to tests.yaml |
| `--output` | `-o` | Output JSON file (default: results.json) |
| `--details` | `-d` | Show full responses in console |
| `--model` | `-m` | Run only on this model (by name) |
| `--test` | | Run only this test (by ID) |

## Output

### Console

Summary table with scores and times, plus per-model averages:

```
LLM Evaluation Results
+--------------------------------------------------+
| Model              | Test       | Score | Time  |
|--------------------+------------+-------+-------|
| gemma4-E4B (Local) | logic_01   |    10 | 186s  |
| gemma4-E4B (Local) | code_01    |    10 |  12s  |
+--------------------------------------------------+

Average Scores by Model
+-------------------------------------------+
| Model              | Avg Score | Avg Time |
|--------------------+-----------+----------|
| gemma4-E4B (Local) |      10.0 |    99.0s |
+-------------------------------------------+
```

### JSON (`results.json`)

Full results including raw prompts, responses, judge scores, justifications, and timing data. Saved incrementally after each test so nothing is lost on crash.

## Server Management

The evaluator automatically:
1. Starts `llama-server` for each unique `hf_repo` (local or remote)
2. Waits for health check before running tests
3. Stops all servers when done (even on error)

Local server command:
```
llama-server -hf <repo>:<quant> --host 127.0.0.1 --port <port> -ctk q8_0 -ctv q8_0 --no-mmproj
```

Remote (via `llamaremote`):
```
ssh user@host "llamacpp -hf <repo>:<quant> --host 0.0.0.0 --port <port>"
```

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
