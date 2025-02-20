#!/usr/bin/env python3
# krs.py

import typer
import os
import subprocess
import asyncio
import time
from krs.main import KrsMain
from krs.utils.constants import KRSSTATE_PICKLE_FILEPATH, KRS_DATA_DIRECTORY

app = typer.Typer(help="krs: A command-line interface to scan your Kubernetes Cluster, detect errors, provide resolutions using LLMs, and recommend latest tools for your cluster")
krs = KrsMain()

# Function to check if `mcp_server.py` is running
def is_mcp_server_running():
    """Check if MCP server is running on port 8080."""
    output = os.popen("lsof -i :8080").read()
    return "LISTEN" in output  # Returns True if running

def start_mcp_server():
    """Start or restart MCP server if needed."""
    if is_mcp_server_running():
        typer.echo(" MCP server is already running on port 8080.")
    else:
        typer.echo(" Restarting MCP Server...")
        os.system("sudo kill -9 $(lsof -t -i:8080) 2>/dev/null")  # Ensure it's stopped
        subprocess.Popen(["python", "krs/krs/utils/mcp_server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def check_initialized():
    if not os.path.exists(KRSSTATE_PICKLE_FILEPATH):
        typer.echo("KRS is not initialized. Please run 'krs init' first.")
        raise typer.Exit()

if not os.path.exists(KRS_DATA_DIRECTORY):
    os.mkdir(KRS_DATA_DIRECTORY)

@app.command()
def init(kubeconfig: str = typer.Option('~/.kube/config', help="Custom path for kubeconfig file if not default")):
    """Initializes the services and loads the scanner."""
    krs.initialize(kubeconfig)
    typer.echo("Services initialized and scanner loaded.")

@app.command()
def scan():
    """Scans the cluster and extracts a list of tools that are currently used."""
    check_initialized()
    krs.scan_cluster()

@app.command()
def namespaces():
    """Lists all the namespaces."""
    check_initialized()
    namespaces = krs.list_namespaces()
    typer.echo("Namespaces in your cluster are: \n")
    for i, namespace in enumerate(namespaces):
        typer.echo(str(i + 1) + ". " + namespace)

@app.command()
def pods(namespace: str = typer.Option(None, help="Specify namespace to list pods from")):
    """Lists all the pods with namespaces, or lists pods under a specified namespace."""
    check_initialized()
    if namespace:
        pods = krs.list_pods(namespace)
        if pods == 'wrong namespace name':
            typer.echo("\nWrong namespace name entered, try again!\n")
            raise typer.Abort()
        typer.echo(f"\nPods in namespace '{namespace}': \n")
    else:
        pods = krs.list_pods_all()
        typer.echo("\nAll pods in the cluster: \n")
    
    for i, pod in enumerate(pods):
        typer.echo(str(i + 1) + '. ' + pod)

@app.command()
def recommend():
    """Generates a table of recommended tools from our ranking database and their CNCF project status."""
    check_initialized()
    krs.generate_recommendations()

@app.command()
def health(change_model: bool = typer.Option(False, help="Option to reinitialize/change the LLM, if set to True"),
    device: str = typer.Option('cpu', help='Option to run Huggingface models on GPU by entering the option as "gpu"')):
    """Starts an interactive terminal using an LLM of your choice to detect and fix issues with your cluster."""
    check_initialized()
    typer.echo("\nStarting interactive terminal...\n")

    # Automatically start MCP server if not running
    start_mcp_server()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    loop.run_until_complete(krs.health_check(change_model, device))

@app.command()
def export():
    """Exports pod info with logs and events."""
    check_initialized()
    krs.export_pod_info()
    typer.echo("Pod info with logs and events exported. JSON file saved to current directory!")

@app.command()
def exit():
    """Ends KRS services safely and deletes all state files from system. Removes all cached data."""
    check_initialized()
    krs.exit()
    typer.echo("KRS services closed safely.")

if __name__ == "__main__":
    app()
