import json
import os
import time
from typing import List, Dict, Any, Optional
from llm_evaluator.client import OpenAIClient
from llm_evaluator.evaluator import Evaluator

_SYNTH_TEXT = (
    "The sun rose slowly over the distant mountains, casting long golden shadows "
    "across the valley floor. Birds began their morning chorus, filling the crisp air "
    "with melody. A light breeze stirred the leaves of the oak trees lining the path. "
    "Children laughed as they chased each other through the dewy grass. The smell of "
    "fresh bread drifted from the bakery on the corner. Clouds drifted lazily overhead, "
    "their shapes shifting in the gentle wind. A river wound its way through the meadow, "
    "its waters clear and cold. Fishermen stood silently at its banks, waiting patiently. "
) * 300


class Runner:
    """Executes tests against models and evaluates responses."""

    def __init__(
        self,
        system_prompt: str,
        models: List[dict],
        tests: List[dict],
        judge_client: OpenAIClient,
        judge_config: dict,
        results_file: Optional[str] = None,
        test_filter: Optional[str] = None,
    ):
        self.system_prompt = system_prompt
        self.models = models
        self.tests = tests
        self.evaluator = Evaluator(judge_client, judge_config)
        self.results_file = results_file
        self.test_filter = test_filter
        self.results = []

    def _get_filtered_tests(self):
        filtered = self.tests
        if self.test_filter:
            filtered = [t for t in self.tests if t["id"] == self.test_filter]
            if not filtered:
                print(f"  Warning: test '{self.test_filter}' not found, running all tests")
                filtered = self.tests
        return filtered

    def collect_responses(self) -> List[Dict[str, Any]]:
        """Phase 1: run all tests and collect raw model responses (no judging)."""
        filtered_tests = self._get_filtered_tests()
        total = len(self.models) * len(filtered_tests)
        current = 0

        for model in self.models:
            print(f"\n{'=' * 60}")
            print(f"Model: {model['name']}")
            print(f"{'=' * 60}")

            for test in filtered_tests:
                current += 1
                print(f"\n  [{current}/{total}] Running test: {test['id']} ({test['category']})")

                if test.get("benchmark"):
                    result = self._run_benchmark(model, test, current, total)
                    self.results.append(result)
                    self._save_incremental()
                    continue

                try:
                    test_start = time.time()
                    response, usage = self._run_test(model, test)
                    model_elapsed = time.time() - test_start
                    completion_tokens = usage.get("completion_tokens", 0)
                    gen_tps = round(completion_tokens / model_elapsed, 2) if model_elapsed > 0 and completion_tokens else None
                    if gen_tps:
                        print(f"  Response received in {model_elapsed:.1f}s — {completion_tokens} tokens — {gen_tps} tk/s")
                    else:
                        print(f"  Response received in {model_elapsed:.1f}s")
                    result = {
                        "model_name": model["name"],
                        "model_type": model["type"],
                        "server_cmd": model.get("server_cmd"),
                        "test_id": test["id"],
                        "category": test["category"],
                        "test_prompt": test["prompt"],
                        "evaluation_criteria": test["evaluation_criteria"],
                        "model_response": response,
                        "response_time": round(model_elapsed, 2),
                        "completion_tokens": completion_tokens,
                        "gen_tps": gen_tps,
                    }
                except Exception as e:
                    print(f"  ERROR: {e}")
                    result = {
                        "model_name": model["name"],
                        "model_type": model["type"],
                        "server_cmd": model.get("server_cmd"),
                        "test_id": test["id"],
                        "category": test["category"],
                        "test_prompt": test["prompt"],
                        "evaluation_criteria": test.get("evaluation_criteria", ""),
                        "model_response": f"ERROR: {e}",
                        "response_time": 0,
                    }
                self.results.append(result)
                self._save_incremental()
        return self.results

    def _run_benchmark(self, model, test, current, total) -> Dict[str, Any]:
        """Run a synthetic benchmark test and return timing results."""
        n_prompt = test["n_prompt"]
        n_generate = test["n_generate"]
        print(f"\n  [{current}/{total}] Benchmark: {test['id']} (prompt={n_prompt}, gen={n_generate})")
        client = model["client"]
        try:
            tokens = client.tokenize(_SYNTH_TEXT)[:n_prompt]
            prompt = client.detokenize(tokens)
            timings = client.benchmark(prompt, n_predict=n_generate)
            pp_tps = timings.get("prompt_per_second")
            tg_tps = timings.get("predicted_per_second") if n_generate > 0 else None
            elapsed = (timings.get("prompt_ms", 0) + timings.get("predicted_ms", 0)) / 1000
            summary = f"pp={pp_tps:.1f} tk/s" if pp_tps else ""
            if tg_tps:
                summary += f"  tg={tg_tps:.1f} tk/s"
            print(f"  {summary} ({timings.get('prompt_n', 0)} prompt tokens, {timings.get('predicted_n', 0)} generated)")
            n_prompt_actual = timings.get("prompt_n", 0)
            n_generate_actual = timings.get("predicted_n", 0)
        except Exception as e:
            print(f"  TIMEOUT/ERROR: {e}")
            pp_tps = tg_tps = None
            elapsed = 0
            summary = "timeout"
            n_prompt_actual = n_prompt
            n_generate_actual = 0
        return {
            "model_name": model["name"],
            "model_type": model["type"],
            "server_cmd": model.get("server_cmd"),
            "test_id": test["id"],
            "category": "benchmark",
            "benchmark": True,
            "n_prompt_actual": n_prompt_actual,
            "n_generate_actual": n_generate_actual,
            "pp_tps": round(pp_tps, 2) if pp_tps else None,
            "tg_tps": round(tg_tps, 2) if tg_tps else None,
            "response_time": round(elapsed, 2),
            "completion_tokens": n_generate_actual,
            "gen_tps": round(tg_tps, 2) if tg_tps else None,
            "model_response": summary,
            "test_prompt": "",
            "evaluation_criteria": "",
            "evaluation": {"score": -1, "justification": summary, "judge_raw_response": ""},
            "judge_time": 0,
        }

    def evaluate_responses(self) -> List[Dict[str, Any]]:
        """Phase 2: judge all collected responses (requires judge_client set)."""
        total = len(self.results)
        for i, result in enumerate(self.results):
            if result.get("benchmark"):
                continue
            print(f"\n  [{i+1}/{total}] Evaluating: {result['test_id']}")
            if result["model_response"].startswith("ERROR:"):
                result["evaluation"] = {
                    "score": 0,
                    "justification": f"Test failed: {result['model_response']}",
                    "judge_raw_response": "",
                }
                result["judge_time"] = 0
            else:
                try:
                    eval_start = time.time()
                    evaluation = self.evaluator.evaluate(
                        test_prompt=result["test_prompt"],
                        model_response=result["model_response"],
                        evaluation_criteria=result["evaluation_criteria"],
                    )
                    result["evaluation"] = evaluation
                    result["judge_time"] = round(time.time() - eval_start, 2)
                    print(f"  Score: {evaluation['score']}/10")
                except Exception as e:
                    print(f"  ERROR evaluating: {e}")
                    result["evaluation"] = {
                        "score": 0,
                        "justification": f"Evaluation failed: {e}",
                        "judge_raw_response": "",
                    }
                    result["judge_time"] = 0
            self._save_incremental()
        return self.results

    def run(self) -> List[Dict[str, Any]]:
        """Run all tests against all models and evaluate responses."""
        self.collect_responses()
        return self.evaluate_responses()

        return self.results

    def _run_test(self, model: dict, test: dict) -> str:
        """Run a single test against a model."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": test["prompt"]},
        ]
        return model["client"].chat(
            messages=messages,
            model=model.get("model", "local"),
            max_tokens=model.get("max_tokens", 2048),
        )  # returns (content, usage)

    def _save_incremental(self):
        """Save results after each test so nothing is lost on crash."""
        if not self.results_file:
            return
        out_dir = os.path.dirname(self.results_file)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        output = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_tests": len(self.results),
            "results": self.results,
        }
        with open(self.results_file, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
