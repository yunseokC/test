import asyncio
import os
import sys
from typing import Optional
from contextlib import AsyncExitStack
import json
import re
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import logging
from anthropic import Anthropic

class AnthropicClient:
    def __init__(self, device="cpu"):
        """Initialize the KrsGPTClient"""
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.anthropic_api_key =  None 
        self.anthropic = None
        self.device = device
        self.pending_fix_requests = {}  # Stores pods with errors
        self.tools_displayed = False  # Prevents reloading tools on every request
        self.set_anthropic_api_key()


    def set_anthropic_api_key(self, api_key: Optional[str] = None):
        """Set the Anthropic API key until valid."""
        while True:
            if not api_key:
                api_key = input("Please enter your Anthropic API key: ").strip()
        
            if not api_key:
                print("Anthropic API key cannot be empty. Please enter a valid key.")
                continue
        
            self.anthropic_api_key = api_key
            try:
                self.anthropic = Anthropic(api_key=self.anthropic_api_key)
                # Test API key validity
                try:
                    self.anthropic.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=1, system="Test", messages=[{"role": "user", "content":" test"}])
                except Exception as e:
                    print(f"Invalid API key: {e}. Please try again.")
                    api_key = None  # Reset key and retry
                    continue
                print("Anthropic API key set successfully.")
                break  # Exit loop if successful
            except Exception as e:
                print(f"Invalid API key: {e}. Please try again.")
                api_key = None  # Reset key and retry


    async def connect_to_server(self, server_script_path: str):
        """Connect to an MCP server."""
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python3" if is_python else "node"
        server_params = StdioServerParameters(command=command, args=[server_script_path], env=None)

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))

        await self.session.initialize()

        # Fetch available tools **only once**
        response = await self.session.list_tools()
        available_tools = [tool.name for tool in response.tools]
        formatted_tools = "\n".join([f"  - {tool}" for tool in available_tools])

        print("\nConnected to the MCP Server.")
        print("\nAvailable Tools:\n" + formatted_tools + "\n")

        self.tools_displayed = True  # Mark tools as displayed

    #===========================================================================

    async def process_query(self, query: str) -> str:
        """Processes a user query, determines the best MCP tool to call, and returns a formatted response."""

        if self.session is None:
            return "Error: MCP session is not initialized. Please connect to the server first."

        if self.anthropic is None:
            print("Anthropic API key is missing. Please set it now.")
            self.set_anthropic_api_key()

        try:
            response = await self.session.list_tools()
            available_tools = {tool.name: tool for tool in response.tools}
            #print(f"Available tools: {list(available_tools.keys())}")
        except Exception as e:
            return f"Error retrieving tools: {str(e)}"

        try:
            response = self.anthropic.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=300,
                system="You are a Kubernetes assistant. Decide the best function to call based on user queries.",
                messages=[{"role": "user", "content": f"User query: {query}. Decide which function to call and what arguments are needed."}],
                tools=[{
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema
                } for tool in available_tools.values()]
            )

            chosen_tool = None
            tool_args = {}

            for content in response.content:
                if content.type == "tool_use":
                    chosen_tool = content.name
                    tool_args = content.input
                    break

            if not chosen_tool:
                return "Error: Anthropic could not determine a valid tool for this query."

            if chosen_tool not in available_tools:
                return f"Error: `{chosen_tool}` is not a valid tool. Available tools: {list(available_tools.keys())}"


            try:
                
                tool_result = await asyncio.wait_for(self.session.call_tool(chosen_tool, tool_args), timeout=30)

                if not tool_result:
                    return "Error: MCP did not return a response, but the pod may have been created."
                
                return self.format_response(chosen_tool, tool_result)

            except asyncio.TimeoutError:
                return "Error: The tool request timed out."
            
            except Exception as e:
                return f"Error calling tool: {str(e)}"


        except Exception as e:
            return f"Error processing query with Anthropic: {str(e)}"
        


# ============================================


    def format_response(self, tool_name, tool_result):
        """Formats the tool response in a structured and readable manner."""

        tool_name_formatted = tool_name.replace("_", " ").title()

        # Extract text from TextContent objects
        if hasattr(tool_result, "content") and isinstance(tool_result.content, list):
            text_content = "\n".join([content.text for content in tool_result.content if hasattr(content, "text")])
        elif isinstance(tool_result, str):
            text_content = tool_result
        else:
            return str(tool_result)  # Default case

        # Check if the response is a list of pod statuses
        lines = text_content.strip().split("\n")
        if len(lines) > 1 and all("(" in line and ")" in line for line in lines):
            formatted_result = "\n".join([
                f"{line.split(' (')[0]:<30} {line.split(' (')[1].replace(')', ''):<15}" for line in lines
            ])
            return f"\n{tool_name_formatted}:\n{'Pod Name':<30} {'Status':<15}\n" + "-" * 50 + f"\n{formatted_result}"

        return f"\n{tool_name_formatted}:\n{text_content}"


    async def interactive_session(self, initial_prompt: str = None):
        """Run an interactive chat loop, handling real-time queries, deletion confirmations, log fixes, and AI suggestions."""
        print("\nMCP Client Started!")
        print("Type your queries or 'quit' to exit.")

        pending_confirmation = None  
        pending_action = None  
        self.pending_fix_requests = {}  

        response = await self.session.list_tools()
        available_tools = {tool.name: tool for tool in response.tools}
        available_tool_names = list(available_tools.keys())

        while True:
            try:
                query = input("\nQuery: ").strip().lower()

                if query == 'quit':
                    print("\nExiting interactive session. Goodbye!")
                    break

                # Step 1: Handle Deletion Confirmation
                if pending_confirmation:
                    if query == "yes":
                        if pending_action == "delete_pod":
                            print(f"Deleting pod: {pending_confirmation}...")

                            # Update the deletion query to use the confirmed pod name 
                            response = await self.process_query(f"delete_pod name={pending_confirmation}")

                            pending_confirmation = None  
                            pending_action = None  

                            print("\n" + response)
                            continue  
                        else:
                            print("I’m not sure what you want to confirm. Please specify.")
                            continue  

                    elif query == "no":
                        print("Action canceled.")
                        pending_confirmation = None  
                        pending_action = None  
                        continue  

                    else:
                        print("I didn't understand. Do you want to proceed with the deletion? Reply 'yes' or 'no'.")
                        continue  

                # Step 2: Process Normal Queries
                response = await self.process_query(query)

                # Step 3: Handle Log Fixing Request
                if "Error retrieving logs" in response:
                    print(response)
                    ai_suggestion = await self.get_error_resolution_from_ai(response) 
                    print("\nAI's step-by-step resolution suggestion:\n" + ai_suggestion)
                    continue  

                # Step 4: Handle Deletion Confirmation Prompt
                if "Did you mean" in response and "Reply 'yes' to delete it" in response:
                    print(response)
                    suggested_pod_name = response.split("'")[3]  

                    # Update pending confirmation to the suggested pod name
                    pending_confirmation = suggested_pod_name  
                    pending_action = "delete_pod"  
                    continue  

                # Step 5: Handle Unrecognized Queries - Provide Suggestions
                if "could not determine a valid tool" in response.lower() or "help" in query.lower():
                    print("\nError: AI could not determine a valid action for this query.")
                    print("\nHere are some available commands you can use:")
                    for suggestion in available_tool_names[:10]:  # Show top 10 available commands
                        print(f"  - {suggestion.replace('_', ' ')}")
                    continue  

                print("\n" + response)

            except Exception as e:
                print(f"\nError: {str(e)}")




    async def get_error_resolution_from_ai(self, error_message: str) -> str:
        """Process the error message from the server and ask the AI for step-by-step resolutions."""
        try:
            response = self.anthropic.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=300,
                system="You are a Kubernetes assistant. Based on the provided error logs, suggest a step-by-step troubleshooting process.",
                messages=[{"role": "user", "content": f"Error detected in Kubernetes pod logs: {error_message}. Suggest a step-by-step troubleshooting process."}]
            )
            ai_suggestion = response.content[0].text

            return ai_suggestion

        except Exception as e:
            # Handle any errors during the AI request
            return f"Error requesting AI resolution: {str(e)}"





async def main():
    """Main function to start the client"""
    if len(sys.argv) < 2:
        print("Usage: python client.py <path_to_server_script>")
        sys.exit(1)

    client = AnthropicClient()
    client.set_anthropic_api_key()
    try:
        await client.connect_to_server(sys.argv[1])
        await client.interactive_session()
    finally:
        await client.exit_stack.aclose()    

if __name__ == "__main__":
    asyncio.run(main())
