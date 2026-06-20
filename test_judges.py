"""Test that all 3 judge providers can evaluate a response.

Exercises the real Evaluator.evaluate() code path for each judge preset in
config.yaml's `judges:` section:
  - local       -> OpenAI-compatible client to a local llama-server (port 8081)
  - claude-code -> `claude --print` CLI subprocess (no API key)
  - openrouter  -> OpenAI-compatible client to OpenRouter (key from ~/openrouter.apikey)
"""
import os
import sys
import yaml

# OpenRouter key lives in a file, not the environment. Load it so that
# resolve_env_vars can expand ${OPENROUTER_API_KEY} in config.yaml.
key_path = os.path.expanduser("~/openrouter.apikey")
if os.path.exists(key_path):
    with open(key_path) as f:
        os.environ["OPENROUTER_API_KEY"] = f.read().strip()

from llm_evaluator.config_loader import resolve_env_vars
from llm_evaluator.client import OpenAIClient
from llm_evaluator.server_manager import ServerManager
from llm_evaluator.evaluator import Evaluator

# A trivial sample that any competent judge should score highly.
SAMPLE = {
    "test_prompt": "What is the capital of France? Answer in one word.",
    "model_response": "Paris",
    "evaluation_criteria": "The answer must correctly identify Paris as the capital of France.",
}


def main():
    with open("config.yaml", encoding="utf-8") as f:
        config = resolve_env_vars(yaml.safe_load(f))
    judges = config["judges"]

    only = sys.argv[1:] or ["claude-code", "openrouter", "local"]
    server_mgr = ServerManager()
    summary = []

    try:
        for name in only:
            judge_config = judges[name]
            print(f"\n{'=' * 60}\nJUDGE: {name} "
                  f"(provider={judge_config.get('provider', judge_config.get('type'))})\n{'=' * 60}")

            client = None
            if judge_config.get("type") in ("local", "remote") and not judge_config.get("provider"):
                if judge_config["type"] == "local":
                    client = server_mgr.start_local(
                        hf_repo=judge_config["hf_repo"],
                        port=judge_config.get("port", 8081),
                        ctx_size=judge_config.get("ctx_size"),
                        no_warmup=judge_config.get("no_warmup", False),
                    )
            elif judge_config.get("provider") == "openrouter":
                client = OpenAIClient(
                    base_url=judge_config.get("base_url", "https://openrouter.ai/api/v1"),
                    api_key=judge_config.get("api_key"),
                )

            try:
                evaluator = Evaluator(client, judge_config)
                result = evaluator.evaluate(**SAMPLE)
                print(f"  score={result['score']}/10  justification={result['justification']!r}")
                summary.append((name, "OK", result["score"]))
            except Exception as e:
                print(f"  FAILED: {e}")
                summary.append((name, f"FAILED: {e}", None))
            finally:
                if client:
                    client.close()
    finally:
        server_mgr.stop_all()

    print(f"\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    for name, status, score in summary:
        print(f"  {name:14} {status}" + (f"  (score {score}/10)" if score is not None else ""))


if __name__ == "__main__":
    main()
