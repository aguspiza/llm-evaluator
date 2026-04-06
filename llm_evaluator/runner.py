import json
import os
import time
from typing import List, Dict, Any, Optional
from llm_evaluator.client import OpenAIClient
from llm_evaluator.evaluator import Evaluator


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

    def run(self) -> List[Dict[str, Any]]:
        """Run all tests against all models and evaluate responses."""
        filtered_tests = self.tests
        if self.test_filter:
            filtered_tests = [t for t in self.tests if t["id"] == self.test_filter]
            if not filtered_tests:
                print(
                    f"  Warning: test '{self.test_filter}' not found, running all tests"
                )
                filtered_tests = self.tests

        total = len(self.models) * len(filtered_tests)
        current = 0

        for model in self.models:
            print(f"\n{'=' * 60}")
            print(f"Model: {model['name']}")
            print(f"{'=' * 60}")

            for test in filtered_tests:
                current += 1
                print(
                    f"\n  [{current}/{total}] Running test: {test['id']} ({test['category']})"
                )

                try:
                    test_start = time.time()
                    response = self._run_test(model, test)
                    model_elapsed = time.time() - test_start

                    print(f"  Response received in {model_elapsed:.1f}s")
                    print(f"  Evaluating...")

                    eval_start = time.time()
                    evaluation = self.evaluator.evaluate(
                        test_prompt=test["prompt"],
                        model_response=response,
                        evaluation_criteria=test["evaluation_criteria"],
                    )
                    judge_elapsed = time.time() - eval_start

                    total_elapsed = time.time() - test_start

                    result = {
                        "model_name": model["name"],
                        "model_type": model["type"],
                        "test_id": test["id"],
                        "category": test["category"],
                        "test_prompt": test["prompt"],
                        "model_response": response,
                        "evaluation": evaluation,
                        "response_time": round(model_elapsed, 2),
                        "judge_time": round(judge_elapsed, 2),
                        "total_time": round(total_elapsed, 2),
                    }
                    self.results.append(result)
                    self._save_incremental()
                    print(
                        f"  Score: {evaluation['score']}/10 | Total: {total_elapsed:.1f}s"
                    )

                except Exception as e:
                    print(f"  ERROR: {e}")
                    result = {
                        "model_name": model["name"],
                        "model_type": model["type"],
                        "test_id": test["id"],
                        "category": test["category"],
                        "test_prompt": test["prompt"],
                        "model_response": f"ERROR: {e}",
                        "evaluation": {
                            "score": 0,
                            "justification": f"Test failed: {e}",
                            "judge_raw_response": "",
                        },
                        "response_time": 0,
                    }
                    self.results.append(result)
                    self._save_incremental()

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
        )

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
