from fastapi import FastAPI
from pydantic import BaseModel
from kubernetes import client, config
from typing import List
from mcp.server.fastmcp import FastMCP
import logging
import asyncio
import concurrent.futures
import string, random
from difflib import get_close_matches
import re

# Load Kubernetes configuration
config.load_kube_config()
v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()


# Initialize FastAPI
app = FastAPI(
    title="MCP Kubernetes Server",
    description="A custom server using MCP Python SDK for Kubernetes resource management",
    version="1.1.0"
)

# Initialize MCP server
mcp = FastMCP("Kubernetes Management Server")

# Logging filter
logging.basicConfig(level=logging.ERROR)
for logger_name in ["mcp", "uvicorn", "uvicorn.access"]:
    logging.getLogger(logger_name).setLevel(logging.ERROR)

# Define Pydantic models
class PodManifest(BaseModel):
    apiVersion: str = "v1"
    kind: str = "Pod"
    metadata: dict
    spec: dict

### LIST RESOURCES ###

@mcp.tool(name="list_pods", description="List all pods in a Kubernetes namespace.")
def list_pods(namespace: str = "default") -> str:
    """List all running pods in a namespace"""
    try:
        pods = v1.list_namespaced_pod(namespace)
        if not pods.items:
            return "No pods found in the namespace."
        return "\n".join([f"{pod.metadata.name} ({pod.status.phase})" for pod in pods.items])
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(name="list_namespaces", description="List all namespaces in the cluster.")
def list_namespaces() -> str:
    """List all namespaces"""
    try:
        namespaces = v1.list_namespace()
        return "\n".join([ns.metadata.name for ns in namespaces.items])
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(name="list_services", description="List all services in a namespace.")
def list_services(namespace: str = "default") -> str:
    """List all services in the specified namespace"""
    try:
        services = v1.list_namespaced_service(namespace)
        if not services.items:
            return "No services found in the namespace."
        return "\n".join([svc.metadata.name for svc in services.items])
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(name="list_deployments", description="List all deployments in a namespace.")
def list_deployments(namespace: str = "default") -> str:
    """List all deployments in the specified namespace"""
    try:
        deployments = apps_v1.list_namespaced_deployment(namespace)
        if not deployments.items:
            return "No deployments found in the namespace."
        return "\n".join([dep.metadata.name for dep in deployments.items])
    except Exception as e:
        return f"Error: {str(e)}"







@mcp.tool(name="create_pod", description="Create Kubernetes pods with AI-assisted configuration.")
async def create_pod(names: str, namespace: str = "default") -> str:
    """Creates pods, ensuring unique name, image, and ports"""
    created_pods, skipped_pods, failed_pods = [], [], []

    # Normalize input: Split by commas and remove extra spaces around pod names
    pod_names = [name.strip() for name in names.split(',') if name.strip()]
    
    loop = asyncio.get_running_loop()
    executor = concurrent.futures.ThreadPoolExecutor()

    existing_pods = {pod.metadata.name: pod for pod in v1.list_namespaced_pod(namespace).items}

    processed_pod_manifest = []
    for pod_name in pod_names:
        # Generate pod spec if only the name is provided
        pod_spec = _generate_pod_spec(pod_name, existing_pods)

        pod_spec = _fill_missing_pod_info(pod_spec, existing_pods)  # Fill in missing details

        if _is_duplicate_pod(pod_spec, existing_pods):  # Check if pod already exists
            skipped_pods.append(pod_spec["metadata"]["name"])
        else:
            processed_pod_manifest.append(pod_spec)

    # Create pods asynchronously
    tasks = [loop.run_in_executor(executor, _create_single_pod, pod, created_pods, skipped_pods, failed_pods)
            for pod in processed_pod_manifest]

    await asyncio.gather(*tasks, return_exceptions=True)

    response = []
    if created_pods:
        response.append(f"Created pods: {', '.join(created_pods)}")
    if skipped_pods:
        response.append(f"Skipped existing pods: {', '.join(skipped_pods)}")
    if failed_pods:
        response.append(f"Failed to create: {', '.join(failed_pods)}")

    return "\n".join(response)






### HELPER FUNCTIONS FOR POD CREATION ###
def _generate_pod_spec(pod_name, existing_pods):
    """Generate full pod spec if only a name is given"""
    pod_image = "nginx:latest"
    pod_port = _generate_unique_port(existing_pods)

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": pod_name},
        "spec": {
            "containers": [
                {
                    "name": pod_name,
                    "image": pod_image,
                    "ports": [{"containerPort": pod_port}]
                }
            ]
        }
    }


def _fill_missing_pod_info(pod, existing_pods):
    """Ensure pod contains required details"""
    if "metadata" not in pod or "name" not in pod["metadata"]:
        pod["metadata"]["name"] = _generate_unique_pod_name(existing_pods)

    if "spec" not in pod:
        pod["spec"] = {}

    if "containers" not in pod["spec"] or not pod["spec"]["containers"]:
        pod["spec"]["containers"] = [{
            "name": pod["metadata"]["name"],
            "image": "nginx:latest",
            "ports": [{"containerPort": _generate_unique_port(existing_pods)}]
        }]
    else:
        # Ensure each container has a port definition
        for container in pod["spec"]["containers"]:
            if "ports" not in container:
                container["ports"] = [{"containerPort": _generate_unique_port(existing_pods)}]

    return pod



def _generate_unique_pod_name(existing_pods):
    """Generate a unique pod name to avoid conflicts."""
    while True:
        random_name = "pod-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
        if random_name not in existing_pods:
            return random_name


def _generate_unique_port(existing_pods):
    """Generate a unique port number to avoid conflicts."""
    used_ports = set()
    for pod in existing_pods.values():
        for container in pod.spec.containers:
            for port in (container.ports or []):
                used_ports.add(port.container_port)

    new_port = 8080
    while new_port in used_ports:
        new_port += 1
    return new_port


def _is_duplicate_pod(new_pod, existing_pods):
    """Check if a pod with the same name, image, and port already exists."""
    for pod in existing_pods.values():
        if pod.metadata.name == new_pod["metadata"]["name"]:
            return True
        for container in pod.spec.containers:
            new_container = new_pod["spec"]["containers"][0]
            if (container.image == new_container["image"] and
                    any(port.container_port == new_container["ports"][0]["containerPort"] for port in container.ports or [])):
                return True
    return False


def _create_single_pod(pod, created_pods, skipped_pods, failed_pods):
    """Create a single pod asynchronously."""
    pod_name = pod["metadata"]["name"]
    try:
        v1.create_namespaced_pod(namespace="default", body=pod)
        created_pods.append(pod_name)
    except Exception as e:
        failed_pods.append(f"{pod_name} ({str(e)})")






@mcp.tool(name="delete_pod", description="Delete one or more Kubernetes pods with confirmation.")
async def delete_pod(names: str, namespace: str = "default") -> str:
    """Delete one or more pods, suggesting names if incorrect and asking for confirmation."""
    try:
        existing_pods = [pod.metadata.name for pod in v1.list_namespaced_pod(namespace).items]

        # Normalize input by replacing 'and' with commas, then split by commas or spaces
        names_normalized = names.replace(" and ", ",") 
        pod_names = [name.strip() for name in re.split(r'[ ,]+', names_normalized) if name.strip()]
        
        deleted_pods = []
        failed_pods = []
        suggestions = {}

        # Iterate through each pod name
        for pod_name in pod_names:
            if pod_name not in existing_pods:
                # Suggest a similar name if pod is not found
                similar_names = get_close_matches(pod_name, existing_pods, n=1, cutoff=0.6)
                if similar_names:
                    suggestions[pod_name] = similar_names[0]
                    continue  

            try:
                v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
                deleted_pods.append(pod_name)
            except Exception as e:
                failed_pods.append(f"{pod_name} ({str(e)})")

        response = []
        if deleted_pods:
            response.append(f"Pods deleted successfully: {', '.join(deleted_pods)}")
        if failed_pods:
            response.append(f"Failed to delete pods: {', '.join(failed_pods)}")

        # If there are any suggestions, return the prompt for confirmation
        if suggestions:
            suggestion_responses = []
            for incorrect_name, suggested_name in suggestions.items():
                suggestion_responses.append(f"Pod '{incorrect_name}' not found. Did you mean '{suggested_name}'? Reply 'yes' to delete it.")
            return "\n".join(suggestion_responses)

        return "\n".join(response) if response else "No pods were deleted."

    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(name="pod_logs", description="Retrieve logs for a Kubernetes pod and auto-fix errors.")
def pod_logs(name: str, namespace: str = "default") -> str:
    """Retrieve pod logs and auto-fix detected errors"""
    try:
        # Attempt to read the pod's logs
        logs = v1.read_namespaced_pod_log(name=name, namespace=namespace)

        # Check if logs are returned and handle errors
        if not logs:
            return f"No logs available for pod '{name}' in namespace '{namespace}'."

        if "Error" in logs:
            return f"Error detected in logs: {logs}"
        
        return logs

    except client.exceptions.ApiException as e:
        # This checks if we failed to get logs due to a specific issue, such as container failure
        if e.status == 400 and "Bad Request" in e.body:
            error_message = f"Error retrieving logs: {e.body}"
            # Return the error message for client-side processing
            return error_message
        elif e.status == 404:
            return f"No pod found with the name '{name}' in the namespace '{namespace}'."
        return f"Error retrieving logs: {str(e)}"

    except Exception as e:
        return f"Unexpected error: {str(e)}"





@mcp.tool(name="analyze_namespace", description="Analyze pod statuses and errors in a namespace.")
def analyze_namespace(namespace: str = "default") -> str:
    """Analyze namespace for pod status, errors, and deployments"""
    try:
        pods = v1.list_namespaced_pod(namespace)
        pod_status_summary = {"Running": 0, "Pending": 0, "Failed": 0, "Unknown": 0}

        for pod in pods.items:
            status = pod.status.phase
            pod_status_summary[status] = pod_status_summary.get(status, 0) + 1

        deployments = apps_v1.list_namespaced_deployment(namespace)
        deployment_count = len(deployments.items)
        available_replicas = sum(dep.status.available_replicas or 0 for dep in deployments.items)

        report = [
            f"Namespace Analysis for '{namespace}':",
            f"- Running Pods: {pod_status_summary['Running']}",
            f"- Pending Pods: {pod_status_summary['Pending']}",
            f"- Failed Pods: {pod_status_summary['Failed']}",
            f"- Unknown State Pods: {pod_status_summary['Unknown']}",
            f"- Deployments Available: {deployment_count}",
            f"- Deployments with Running Replicas: {available_replicas}",
        ]

        return "\n".join(report)

    except Exception as e:
        return f"Error analyzing namespace: {str(e)}"






### FASTAPI ENDPOINTS ###
@app.get("/")
def read_root():
    return {"message": "MCP Kubernetes Server is Running"}

@app.get("/pods")
def get_pods(namespace: str = "default"):
    return list_pods(namespace)

@app.get("/namespaces")
def get_namespaces():
    return list_namespaces()

@app.get("/services")
def get_services(namespace: str = "default"):
    return list_services(namespace)

@app.get("/deployments")
def get_deployments(namespace: str = "default"):
    return list_deployments(namespace)

@app.get("/pod_logs")
def get_pod_logs(name: str, namespace: str = "default"):
    return pod_logs(name, namespace)

@app.post("/analyze_namespace")
def post_analyze_namespace(namespace: str = "default"):
    return analyze_namespace(namespace)



### RUN MCP SERVER ###
if __name__ == "__main__":
    import uvicorn
    mcp.run()
    uvicorn.run(app, host="0.0.0.0", port=8080)
