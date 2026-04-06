import subprocess
import time
import signal
import os
import sys
import threading
from llm_evaluator.client import OpenAIClient


LLAMA_SERVER_EXE = os.path.expanduser(
    "~/Downloads/llama-b8664-bin-win-cpu-x64/llama-server.exe"
)
LLAMAREMOTE_CMD = "llamaremote"
HEALTH_RETRIES = 300
HEALTH_INTERVAL = 2


class ServerManager:
    """Manages local and remote llama-server instances."""

    def __init__(self):
        self.processes = {}

    def start_local(self, hf_repo: str, port: int) -> OpenAIClient:
        """Start a local llama-server and return a client."""
        cmd = [
            LLAMA_SERVER_EXE,
            "-hf",
            hf_repo,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "-ctk",
            "q8_0",
            "-ctv",
            "q8_0",
            "--no-mmproj",
        ]
        print(f"  Starting local llama-server: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.processes[port] = process

        # Stream server output in background thread
        def log_output():
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    print(f"    [server:{port}] {line}")

        thread = threading.Thread(target=log_output, daemon=True)
        thread.start()

        return self._wait_for_server(f"http://127.0.0.1:{port}", port)

    def start_remote(self, host: str, hf_repo: str, port: int) -> OpenAIClient:
        """Start a remote llama-server via SSH and return a client."""
        cmd = [
            LLAMAREMOTE_CMD,
            host,
            "-hf",
            hf_repo,
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "&",
        ]
        print(f"  Starting remote llama-server via SSH: {' '.join(cmd)}")
        process = subprocess.Popen(cmd)
        self.processes[port] = process
        return self._wait_for_server(f"http://{host}:{port}", port)

    def stop_all(self):
        """Stop all managed servers."""
        for port, process in self.processes.items():
            try:
                if sys.platform == "win32":
                    process.terminate()
                else:
                    os.kill(process.pid, signal.SIGTERM)
                print(f"  Stopped server on port {port}")
            except Exception as e:
                print(f"  Error stopping server on port {port}: {e}")
        self.processes.clear()

    def _wait_for_server(self, base_url: str, port: int) -> OpenAIClient:
        """Poll the health endpoint until the server is ready."""
        client = OpenAIClient(base_url)
        for i in range(HEALTH_RETRIES):
            if client.health_check():
                print(f"  Server ready on port {port}")
                return client
            time.sleep(HEALTH_INTERVAL)
            if (i + 1) % 15 == 0:
                print(
                    f"  Waiting for server on port {port}... ({i + 1}/{HEALTH_RETRIES}) - downloading model may take a while"
                )
        raise TimeoutError(f"Server on port {port} did not start in time")
