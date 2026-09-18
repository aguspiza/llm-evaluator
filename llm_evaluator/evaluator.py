import json
import os
import re
from typing import Optional
from llm_evaluator.client import OpenAIClient


JUDGE_SYSTEM_PROMPT = """Eres un evaluador objetivo de modelos de lenguaje. Tu tarea es analizar respuestas y asignar un score numerico del 0 al 10.
Responde SOLO en formato JSON con las claves "score" (numero entero) y "justification" (texto explicativo conciso en espanol).
No incluyas ningun otro texto fuera del JSON."""

# Jev (TypeSafe System One) judge: a Score question over an ordered rubric.
# Level i maps to grade i * 10 / (len - 1), so the probability-weighted level
# returned by the model becomes a 0-10 grade comparable with the LLM judges.
JEV_INSTRUCTIONS = (
    "Grade `model_response` as an answer to `test_prompt`, applying `evaluation_criteria` strictly. "
    "The criteria are written in Spanish and may describe score bands from 0 to 10; "
    "map them onto the levels below."
)
JEV_LEVELS = [
    "Wrong, empty, refuses, or violates the core requirement of the criteria",
    "Mostly wrong: reaches the wrong conclusion or misses most required elements",
    "Partially meets the criteria: correct in part but misses required elements or has significant errors",
    "Meets the main requirement but with notable flaws (missing explanation, extra text where forbidden, a minor error)",
    "Meets the criteria with only minor imperfections",
    "Fully meets every requirement of the criteria with no flaws",
]


def grade_from_score_answer(answer) -> dict:
    """Turn a Jev ScoreAnswer into the evaluation dict used across results."""
    grade = answer.score * 10 / (len(JEV_LEVELS) - 1)
    probs = {str(k): round(v, 3) for k, v in answer.probabilities.items()}
    return {
        "score": int(round(grade)),
        "justification": f"Jev score {grade:.2f}/10 (confidence {answer.confidence:.2f}); p={probs}",
        "judge_raw_response": json.dumps(
            {"score": answer.score, "confidence": answer.confidence, "probabilities": probs}
        ),
        "jev_score": round(grade, 2),
        "jev_confidence": round(answer.confidence, 3),
    }


def _typesafe_api_key(judge_config: dict) -> Optional[str]:
    """Key from config, else ~/.typesafe_apikey, else None (SDK reads TYPESAFE_API_KEY)."""
    key = judge_config.get("api_key", "")
    if key and not key.startswith("${"):
        return key
    path = os.path.expanduser("~/.typesafe_apikey")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return None


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
        elif self.provider == "typesafe":
            self._anthropic = None
            self.client = None
            if judge_client is None:
                from typesafe_sdk import TypeSafeClient
                judge_client = TypeSafeClient(
                    api_key=_typesafe_api_key(judge_config),
                    model=judge_config.get("model"),
                )
            self._typesafe = judge_client
        else:
            self._anthropic = None
            self.client = judge_client

    def evaluate(self, test_prompt: str, model_response: str, evaluation_criteria: str) -> dict:
        """Evaluate a single response and return score + justification + raw judge output."""
        if self.provider == "typesafe":
            return self._call_typesafe(test_prompt, model_response, evaluation_criteria)

        user_prompt = f"""--- TEST PROMPT ---
{test_prompt}

--- MODEL RESPONSE ---
{model_response}

--- EVALUATION CRITERIA ---
{evaluation_criteria}

Responde SOLO en formato JSON: {{"score": <0-10>, "justification": "..."}}"""

        if self.provider == "claude-code":
            response = self._call_claude_code(user_prompt)
        elif self.provider == "anthropic":
            response = self._call_anthropic(user_prompt)
        else:
            messages = [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            response, _ = self.client.chat(
                messages=messages,
                model=self.model,
                temperature=self.temperature,
            )

        return self._parse_judge_response(response)

    def _call_claude_code(self, user_prompt: str) -> str:
        import subprocess
        cmd = ["claude", "--print", "--output-format", "text",
               "--system-prompt", JUDGE_SYSTEM_PROMPT]
        if self.model and self.model != "local":
            cmd += ["--model", self.model]
        result = subprocess.run(
            cmd,
            input=user_prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude CLI error: {result.stderr.strip()}")
        return result.stdout.strip()

    def _call_anthropic(self, user_prompt: str) -> str:
        message = self._anthropic.messages.create(
            model=self.model,
            max_tokens=512,
            temperature=self.temperature,
            system=JUDGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text

    def _call_typesafe(self, test_prompt: str, model_response: str, evaluation_criteria: str) -> dict:
        from typesafe_sdk import Score
        response = self._typesafe.system_one(
            state={
                "test_prompt": test_prompt,
                "model_response": model_response,
                "evaluation_criteria": evaluation_criteria,
            },
            questions={"grade": Score(instructions=JEV_INSTRUCTIONS, criteria=JEV_LEVELS)},
        )
        return grade_from_score_answer(response.scores["grade"])

    def _parse_judge_response(self, response: str) -> dict:
        """Extract JSON from the judge response."""
        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        text = re.sub(r'```(?:json)?\s*', '', response).strip()
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
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
