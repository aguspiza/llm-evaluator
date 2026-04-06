import json
import os
from datetime import datetime
from typing import List, Dict, Any
from rich.console import Console
from rich.table import Table
from rich.panel import Panel


console = Console()


class Reporter:
    """Generates reports from evaluation results."""

    def __init__(self, results: List[Dict[str, Any]]):
        self.results = results

    def print_table(self):
        """Print a summary table to console."""
        table = Table(title="LLM Evaluation Results")
        table.add_column("Model", style="cyan", no_wrap=True)
        table.add_column("Test", style="magenta")
        table.add_column("Category", style="yellow")
        table.add_column("Score", style="green", justify="right")
        table.add_column("Time", style="blue", justify="right")
        table.add_column("Justification", style="white")

        for r in self.results:
            total_time = r.get("total_time", r.get("response_time", 0))
            table.add_row(
                r["model_name"],
                r["test_id"],
                r["category"],
                str(r["evaluation"]["score"]),
                f"{total_time:.1f}s",
                r["evaluation"]["justification"][:80]
                + ("..." if len(r["evaluation"]["justification"]) > 80 else ""),
            )

        console.print(table)
        self._print_averages()

    def _print_averages(self):
        """Print average scores per model."""
        model_scores = {}
        model_times = {}
        for r in self.results:
            name = r["model_name"]
            if name not in model_scores:
                model_scores[name] = []
                model_times[name] = []
            model_scores[name].append(r["evaluation"]["score"])
            model_times[name].append(r.get("total_time", r.get("response_time", 0)))

        avg_table = Table(title="Average Scores by Model")
        avg_table.add_column("Model", style="cyan", no_wrap=True)
        avg_table.add_column("Avg Score", style="green", justify="right")
        avg_table.add_column("Avg Time", style="blue", justify="right")
        avg_table.add_column("Tests", style="yellow", justify="right")

        for name, scores in sorted(
            model_scores.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True
        ):
            avg = sum(scores) / len(scores)
            avg_time = sum(model_times[name]) / len(model_times[name])
            avg_table.add_row(name, f"{avg:.1f}", f"{avg_time:.1f}s", str(len(scores)))

        console.print(avg_table)

    def print_details(self, model_filter: str = None):
        """Print detailed results with full responses."""
        for r in self.results:
            if model_filter and r["model_name"] != model_filter:
                continue
            console.print(
                Panel(
                    f"[bold]Model:[/bold] {r['model_name']}\n"
                    f"[bold]Test:[/bold] {r['test_id']} ({r['category']})\n"
                    f"[bold]Score:[/bold] {r['evaluation']['score']}/10\n\n"
                    f"[bold]Prompt:[/bold]\n{r['test_prompt'][:300]}...\n\n"
                    f"[bold]Response:[/bold]\n{r['model_response']}\n\n"
                    f"[bold]Justification:[/bold]\n{r['evaluation']['justification']}",
                    title=f"{r['model_name']} - {r['test_id']}",
                    border_style="green",
                )
            )

    def save_json(self, path: str):
        """Save full results to JSON."""
        out_dir = os.path.dirname(path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        output = {
            "timestamp": datetime.now().isoformat(),
            "total_tests": len(self.results),
            "results": self.results,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        console.print(f"\nResults saved to {path}")
