import typer
import yaml
import os
import sys
from llm_evaluator.config_loader import resolve_env_vars, load_file_content
from llm_evaluator.client import OpenAIClient
from llm_evaluator.server_manager import ServerManager
from llm_evaluator.runner import Runner
from llm_evaluator.reporter import Reporter

app = typer.Typer(help="LLM Evaluator - Automated model benchmarking")


def get_base_dir():
    """Get the directory where config files are located."""
    return os.path.dirname(os.path.abspath(__file__))


def load_config(config_path=None):
    """Load and resolve configuration."""
    if config_path is None:
        config_path = os.path.join(get_base_dir(), "config.yaml")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config = resolve_env_vars(config)
    base_dir = os.path.dirname(os.path.abspath(config_path))
    config["_base_dir"] = base_dir
    return config


def load_tests(tests_path=None, base_dir=None):
    """Load tests and resolve file references."""
    if tests_path is None:
        tests_path = os.path.join(base_dir or get_base_dir(), "tests.yaml")

    with open(tests_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    tests = []
    for t in data.get("tests", []):
        test = {
            "id": t["id"],
            "category": t.get("category", "general"),
            "prompt": load_file_content(t["prompt_file"], base_dir),
            "evaluation_criteria": load_file_content(t["evaluation_file"], base_dir),
        }
        tests.append(test)
    return tests


def start_server(server_mgr, model_config):
    """Start a server for a model config and return the client."""
    mtype = model_config.get("type", "openrouter")
    if mtype == "local":
        return server_mgr.start_local(
            hf_repo=model_config["hf_repo"],
            port=model_config.get("port", 8080),
        )
    elif mtype == "remote":
        return server_mgr.start_remote(
            host=model_config["host"],
            hf_repo=model_config["hf_repo"],
            port=model_config.get("port", 8080),
        )
    elif mtype == "openrouter":
        return OpenAIClient(
            base_url="https://openrouter.ai/api/v1",
            api_key=model_config.get("api_key"),
        )
    return None


def get_judge_client(server_mgr, judge_config, model_clients):
    """Get judge client, reusing an existing server if same hf_repo."""
    if judge_config.get("provider") == "anthropic":
        return None

    jtype = judge_config.get("type", "openrouter")

    if jtype in ("local", "remote"):
        judge_repo = judge_config.get("hf_repo")
        for client in model_clients.values():
            if client.get("hf_repo") == judge_repo:
                return client["client"]

        if jtype == "local":
            return server_mgr.start_local(
                hf_repo=judge_config["hf_repo"],
                port=judge_config.get("port", 8081),
            )
        else:
            return server_mgr.start_remote(
                host=judge_config["host"],
                hf_repo=judge_config["hf_repo"],
                port=judge_config.get("port", 8081),
            )

    return OpenAIClient(
        base_url=judge_config.get("base_url", "https://openrouter.ai/api/v1"),
        api_key=judge_config.get("api_key"),
    )


@app.command()
def run(
    config: str = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
    tests: str = typer.Option(None, "--tests", "-t", help="Path to tests.yaml"),
    output: str = typer.Option(
        "results.json", "--output", "-o", help="Output JSON file"
    ),
    details: bool = typer.Option(
        False, "--details", "-d", help="Show detailed results"
    ),
    model_filter: str = typer.Option(
        None, "--model", "-m", help="Filter by model name"
    ),
    test_filter: str = typer.Option(None, "--test", help="Run only this test by ID"),
):
    """Run all tests against all configured models."""
    config_data = load_config(config)
    base_dir = config_data.pop("_base_dir")

    system_prompt = load_file_content(config_data["system_prompt"], base_dir)
    test_list = load_tests(tests, base_dir)
    models = config_data.get("models", [])
    judge_config = config_data.get("judge", {})

    # Apply model filter early
    if model_filter:
        models = [m for m in models if m["name"] == model_filter]
        if not models:
            print(f"Error: model '{model_filter}' not found")
            raise typer.Exit(1)

    server_mgr = ServerManager()
    model_clients = {}
    judge_client = None

    try:
        # Start ONE server per unique hf_repo + all openrouter clients
        for model in models:
            repo_key = model.get("hf_repo") or model.get("model")
            if repo_key and repo_key in model_clients:
                model["client"] = model_clients[repo_key]["client"]
                continue

            print(f"\n{'#' * 60}")
            print(f"# MODEL: {model['name']}")
            print(f"{'#' * 60}")

            client = start_server(server_mgr, model)
            model["client"] = client
            if repo_key:
                model_clients[repo_key] = {
                    "client": client,
                    "hf_repo": model.get("hf_repo"),
                }

        # Get judge (reuses existing server if same repo)
        judge_client = get_judge_client(server_mgr, judge_config, model_clients)

        # Run ALL tests sequentially on the same servers
        runner = Runner(
            system_prompt,
            models,
            test_list,
            judge_client,
            judge_config,
            results_file=output,
            test_filter=test_filter,
        )
        results = runner.run()

        # Report
        reporter = Reporter(results)
        reporter.print_table()

        if details:
            reporter.print_details(model_filter=model_filter)

        reporter.save_json(output)

    finally:
        for client_info in model_clients.values():
            if hasattr(client_info["client"], "close"):
                client_info["client"].close()
        if judge_client:
            judge_client.close()
        server_mgr.stop_all()


@app.command("list-models")
def list_models(config: str = typer.Option(None, "--config", "-c")):
    """List configured models."""
    from rich.console import Console
    from rich.table import Table

    config_data = load_config(config)
    console = Console()

    table = Table(title="Configured Models")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Model/Repo", style="magenta")
    table.add_column("Port", style="yellow")

    for m in config_data.get("models", []):
        table.add_row(
            m["name"],
            m["type"],
            m.get("model", m.get("hf_repo", "N/A")),
            str(m.get("port", "N/A")),
        )

    console.print(table)


@app.command("list-tests")
def list_tests(
    tests: str = typer.Option(None, "--tests", "-t"),
    config: str = typer.Option(None, "--config", "-c"),
):
    """List configured tests."""
    from rich.console import Console
    from rich.table import Table

    config_data = load_config(config)
    base_dir = config_data.pop("_base_dir")
    test_list = load_tests(tests, base_dir)
    console = Console()

    table = Table(title="Configured Tests")
    table.add_column("ID", style="cyan")
    table.add_column("Category", style="green")
    table.add_column("Prompt (preview)", style="white")

    for t in test_list:
        preview = t["prompt"][:80] + "..." if len(t["prompt"]) > 80 else t["prompt"]
        table.add_row(t["id"], t["category"], preview.replace("\n", " "))

    console.print(table)


if __name__ == "__main__":
    app()
