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
HEALTH_RETRIES = 600
HEALTH_INTERVAL = 2


class ServerManager:
    """Manages local and remote llama-server instances."""

    def __init__(self):
        self.processes = {}
        self.last_cmd: list = []

    def _kill_port(self, port: int):
        """Kill any process listening on the given port (Windows)."""
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    subprocess.run(["taskkill", "/F", "/PID", pid],
                                   capture_output=True)
        except Exception:
            pass

    def start_local(self, hf_repo: str, port: int, ctx_size: int = None, no_warmup: bool = False, exe: str = None, ctk: str = "q4_0", ctv: str = "q4_0", extra_args: list = None) -> OpenAIClient:
        """Start a local llama-server and return a client."""
        self._kill_port(port)
        server_exe = os.path.expanduser(exe) if exe else LLAMA_SERVER_EXE
        cmd = [
            server_exe,
            "-hf",
            hf_repo,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "-ctk",
            ctk,
            "-ctv",
            ctv,
            "--no-mmproj",
        ]
        if ctx_size is not None:
            cmd += ["--ctx-size", str(ctx_size)]
        if no_warmup:
            cmd += ["--no-warmup"]
        if extra_args:
            cmd += extra_args
        self.last_cmd = cmd
        print(f"  Starting local llama-server: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.processes[port] = {"process": process, "host": None}

        # Stream server output in background thread
        def log_output():
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    safe = line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace")
                    print(f"    [server:{port}] {safe}")

        thread = threading.Thread(target=log_output, daemon=True)
        thread.start()

        return self._wait_for_server(f"http://127.0.0.1:{port}", port)

    def _ensure_wsl_portproxy(self, host: str, port: int):
        """Add WSL2 portproxy only if not already configured for this port."""
        check = subprocess.run(
            ["ssh", host, f"netsh interface portproxy show all"],
            capture_output=True, text=True
        )
        if f":{port}" in check.stdout or str(port) in check.stdout:
            print(f"  Portproxy for port {port} already exists, skipping setup")
            return
        result = subprocess.run(
            ["ssh", host, "wsl hostname -I"],
            capture_output=True, text=True
        )
        wsl_ip = result.stdout.strip().split()[0]
        print(f"  WSL2 IP: {wsl_ip} — adding portproxy on {host}:{port}")
        subprocess.run([
            "ssh", host,
            f"netsh interface portproxy add v4tov4 listenport={port} listenaddress=0.0.0.0 connectport={port} connectaddress={wsl_ip}"
        ], capture_output=True)

    def start_remote(self, host: str, hf_repo: str, port: int, exe: str = None, ctk: str = "q4_0", ctv: str = "q4_0", extra_args: list = None, hf_flag: str = "-hf", hf_file: str = None, no_mmproj: bool = True, ctx_size: int = None) -> OpenAIClient:
        """Start a remote llama-server via SSH and return a client."""
        hostname = host.split("@")[-1]
        if exe and exe.startswith("wsl "):
            subprocess.run(["ssh", host, "wsl pkill -f llama-server"], capture_output=True)
        else:
            subprocess.run(["ssh", host, "MSYS_NO_PATHCONV=1 taskkill /F /IM llama-server.exe"], capture_output=True)
        if exe:
            if exe.startswith("wsl "):
                self._ensure_wsl_portproxy(host, port)
            hf_args = [hf_flag, hf_repo]
            if hf_file:
                hf_args += ["-hff", hf_file]
            server_args = hf_args + ["--host", "0.0.0.0", "--port", str(port),
                          "-ctk", ctk, "-ctv", ctv]
            if ctx_size is not None:
                server_args += ["--ctx-size", str(ctx_size)]
            if no_mmproj:
                server_args += ["--no-mmproj"]
            if extra_args:
                server_args += extra_args
            remote_cmd = " ".join([exe] + server_args)
            cmd = ["ssh", host, remote_cmd]
        else:
            cmd = [LLAMAREMOTE_CMD, host, "-hf", hf_repo, "--host", "0.0.0.0", "--port", str(port), "&"]
        self.last_cmd = cmd
        print(f"  Starting remote llama-server via SSH: {' '.join(cmd)}")
        env = os.environ.copy()
        env["MSYS_NO_PATHCONV"] = "1"
        env["MSYS2_ARG_CONV_EXCL"] = "*"
        process = subprocess.Popen(cmd, env=env)
        self.processes[port] = {"process": process, "host": host, "is_wsl": exe and exe.startswith("wsl ")}
        return self._wait_for_server(f"http://{hostname}:{port}", port)

    def stop_all(self):
        """Stop all managed servers."""
        for port, info in self.processes.items():
            process = info["process"]
            host = info.get("host")
            if host:
                if info.get("is_wsl"):
                    subprocess.run(["ssh", host, "wsl pkill -f llama-server"], capture_output=True)
                else:
                    subprocess.run(["ssh", host, "MSYS_NO_PATHCONV=1 taskkill /F /IM llama-server.exe"], capture_output=True)
            try:
                process.terminate()
                print(f"  Stopped server on port {port}")
            except Exception as e:
                print(f"  Error stopping server on port {port}: {e}")
        self.processes.clear()

    def _wait_for_server(self, base_url: str, port: int) -> OpenAIClient:
        """Poll the health endpoint until the server is ready."""
        import httpx
        client = OpenAIClient(base_url)
        for i in range(HEALTH_RETRIES):
            if client.health_check():
                print(f"  Server ready on port {port}")
                return client
            time.sleep(HEALTH_INTERVAL)
            if (i + 1) % 15 == 0:
                try:
                    r = httpx.get(f"{base_url}/health", timeout=5)
                    status_info = f"HTTP {r.status_code} {r.text[:80]}"
                except Exception as e:
                    status_info = f"no response ({e})"
                print(
                    f"  Waiting for server on port {port}... ({i + 1}/{HEALTH_RETRIES}) — {status_info}"
                )
        raise TimeoutError(f"Server on port {port} did not start in time")
