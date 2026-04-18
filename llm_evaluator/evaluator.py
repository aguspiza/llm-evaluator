import json
import os
import re
from typing import Optional
from llm_evaluator.client import OpenAIClient


JUDGE_SYSTEM_PROMPT = """Eres un evaluador objetivo de modelos de lenguaje. Tu tarea es analizar respuestas y asignar un score numerico del 0 al 10.
Responde SOLO en formato JSON con las claves "score" (numero entero) y "justification" (texto explicativo conciso en espanol).
No incluyas ningun otro texto fuera del JSON."""


class Evaluator:
    """Evaluates model responses using a judge model."""

    def __init__(self, judge_client: Optional[OpenAIClient], judge_config: dict):
        self.provider = judge_config.get("provider", "local")
        self.model = judge_config.get("model", "local")
        self.temperature = judge_config.get("temperature", 0.1)

        if self.provider == "anthropic":
            import anthropic
            self._anthropic = anthropic.Anthropic(
                api_key=os.environ["ANTHROPIC_API_KEY"]
            )
            self.client = None
        else:
            self._anthropic = None
            self.client = judge_client

    def evaluate(self, test_prompt: str, model_response: str, evaluation_criteria: str) -> dict:
        """Evaluate a single response and return score + justification + raw judge output."""
        user_prompt = f"""--- TEST PROMPT ---
{test_prompt}

--- MODEL RESPONSE ---
{model_response}

--- EVALUATION CRITERIA ---
{evaluation_criteria}

Responde SOLO en formato JSON: {{"score": <0-10>, "justification": "..."}}"""

        if self.provider == "anthropic":
            response = self._call_anthropic(user_prompt)
        else:
            messages = [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            response = self.client.chat(
                messages=messages,
                model=self.model,
                temperature=self.temperature,
            )

        return self._parse_judge_response(response)

    def _call_anthropic(self, user_prompt: str) -> str:
        message = self._anthropic.messages.create(
            model=self.model,
            max_tokens=512,
            temperature=self.temperature,
            system=JUDGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text

    def _parse_judge_response(self, response: str) -> dict:
        """Extract JSON from the judge response."""
        json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return {
                    "score": int(data.get("score", 0)),
                    "justification": data.get("justification", "No justification provided"),
                    "judge_raw_response": response,
                }
            except (json.JSONDecodeError, ValueError):
                pass
        return {
            "score": 0,
            "justification": f"Failed to parse judge response: {response[:200]}",
            "judge_raw_response": response,
        }
