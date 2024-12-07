# Standard library imports
import itertools
import json
import shutil
import sys
import threading
import subprocess
import time
import urllib.parse
import urllib.request

# Third-party imports
from openai import OpenAI

# Initialize client
client = OpenAI(base_url="http://0.0.0.0:1234/v1", api_key="lm-studio")
MODEL = "qwen2.5-7b-instruct"

# Define tools
WIKI_TOOL = {
        "type": "function",
        "function": {
            "name": "fetch_wikipedia_content",
            "description": (
                "Search Wikipedia and fetch the introduction of the most relevant article. "
                "Always use this if the user is asking for something that is likely on wikipedia. "
                "If the user has a typo in their search query, correct it before searching."
                ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {
                        "type": "string",
                        "description": "Search query for finding the Wikipedia article",
                        },
                    },
                "required": ["search_query"],
                },
            },
        }

DORF_TOOL = {
        "type": "function",
        "function": {
            "name": "dorfer",
            "description": (
                "You are an ancient dwarf with infinite wisdom from the past and future tasked to translate regular language responses to dwarvish"
                "Always use this as the last step in the pipeline to translate the final response to dwarvish."
                ),
            "parameters": {
                "type": "object",
                "properties": {
                    "llm_response": {
                        "type": "string",
                        "description": "The response from the LLM model",
                        },
                    },
                "required": ["llm_response"],
                },
            },
        }

SPEAK_TOOL = {
        "type": "function",
        "function": {
            "name": "speak",
            "description": (
                "You are an ancient dwarf with infinite wisdom from the past and future tasked to interpret the response and speak it out loud."
                "You do this by summarising the content with your dwarvish wisdom."
                ),
            "parameters": {
                "type": "object",
                "properties": {
                    "speak_response": {
                        "type": "string",
                        "description": "The response from the LLM model",
                        },
                    },
                "required": ["speak_response"],
                },
            },
        }

TOOLS = [WIKI_TOOL, DORF_TOOL, SPEAK_TOOL]

# Define tool functions

def speak(content: str) -> dict:
    print(f"SPEEEEK called with: {content}")
    # Construct the TTS command
    command = f'echo "{content}" | mimic3 --stdout | aplay'
    try:
        # Execute the command using subprocess
        subprocess.run(command, check=True, shell=True)
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while executing the TTS command: {e}")

    return {
            "status": "success",
            "content": content,
            "title": 'Dwarvish Speech',
            }

def dorfer(content: str) -> dict:
    print("Dorfer called")
    return {
            "status": "success",
            "content": "dorfdorfdorf",  #content,
            "title": 'Dwarvish Translation',
            }

def fetch_wikipedia_content(search_query: str) -> dict:
    """Fetches wikipedia content for a given search_query"""
    try:
        # Search for most relevant article
        search_url = "https://en.wikipedia.org/w/api.php"
        search_params = {
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": search_query,
                "srlimit": 1,
                }

        url = f"{search_url}?{urllib.parse.urlencode(search_params)}"
        with urllib.request.urlopen(url) as response:
            search_data = json.loads(response.read().decode())

        if not search_data["query"]["search"]:
            return {
                    "status": "error",
                    "message": f"No Wikipedia article found for '{search_query}'",
                    }

        # Get the normalized title from search results
        normalized_title = search_data["query"]["search"][0]["title"]

        # Now fetch the actual content with the normalized title
        content_params = {
                "action": "query",
                "format": "json",
                "titles": normalized_title,
                "prop": "extracts",
                "exintro": "true",
                "explaintext": "true",
                "redirects": 1,
                }

        url = f"{search_url}?{urllib.parse.urlencode(content_params)}"
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read().decode())

        pages = data["query"]["pages"]
        page_id = list(pages.keys())[0]

        if page_id == "-1":
            return {
                    "status": "error",
                    "message": f"No Wikipedia article found for '{search_query}'",
                    }

        content = pages[page_id]["extract"].strip()
        return {
                "status": "success",
                "content": content,
                "title": pages[page_id]["title"],
                }

    except Exception as e:
        return {"status": "error", "message": str(e)}



# Class for displaying the state of model processing
class Spinner:
    def __init__(self, message="Processing..."):
        self.spinner = itertools.cycle(["-", "/", "|", "\\"])
        self.busy = False
        self.delay = 0.1
        self.message = message
        self.thread = None

    def write(self, text):
        sys.stdout.write(text)
        sys.stdout.flush()

    def _spin(self):
        while self.busy:
            self.write(f"\r{self.message} {next(self.spinner)}")
            time.sleep(self.delay)
        self.write("\r\033[K")  # Clear the line

    def __enter__(self):
        self.busy = True
        self.thread = threading.Thread(target=self._spin)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.busy = False
        time.sleep(self.delay)
        if self.thread:
            self.thread.join()
        self.write("\r")  # Move cursor to beginning of line

def handle_tool_calls(client, messages, model, tools):
    while True:
        response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                )

        if response.choices[0].message.tool_calls:
            tool_calls = response.choices[0].message.tool_calls

            # Add all tool calls to messages
            messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": tool_call.id,
                                "type": tool_call.type,
                                "function": tool_call.function,
                                }
                            for tool_call in tool_calls
                            ],
                        }
                    )

            # Process each tool call and add results
            for tool_call in tool_calls:
                print(f"\nAssistant: Processing tool call '{tool_call.function.name}'")
                if tool_call.function.name == "dorfer":
                    args = json.loads(tool_call.function.arguments)
                    result = dorfer(args["llm_response"])
                    messages.append(
                            {
                                "role": "tool",
                                "content": json.dumps(result),
                                "tool_call_id": tool_call.id,
                                }
                            )
                elif tool_call.function.name == "speak":
                    args = json.loads(tool_call.function.arguments)
                    result = speak(args["speak_response"])
                    messages.append(
                            {
                                "role": "tool",
                                "content": json.dumps(result),
                                "tool_call_id": tool_call.id,
                                }
                            )
                elif tool_call.function.name == "fetch_wikipedia_content":
                    args = json.loads(tool_call.function.arguments)
                    result = fetch_wikipedia_content(args["search_query"])

                    # Print the Wikipedia content in a formatted way
                    terminal_width = shutil.get_terminal_size().columns
                    print("\n" + "=" * terminal_width)
                    if result["status"] == "success":
                        print(f"\nWikipedia article: {result['title']}")
                        print("-" * terminal_width)
                        print(result["content"])
                    else:
                        print(
                                f"\nError fetching Wikipedia content: {result['message']}"
                                )
                    print("=" * terminal_width + "\n")

                    messages.append(
                            {
                                "role": "tool",
                                "content": json.dumps(result),
                                "tool_call_id": tool_call.id,
                                }
                            )
        else:
            # Handle regular response
            print("\nAssistant:", response.choices[0].message.content)
            return response.choices[0].message.content


def chat_loop():
    messages =  [
            {
                "role": "system",
                "content": ( "You are an assistant that can retrieve Wikipedia articles, translate text using dwarvish, and speak responses out loud. "
                    "When asked about a topic, you can perform the following actions:"
                    "- Translate text using the 'dorfer' tool."
                    "- Search Wikipedia using the 'fetch_wikipedia_content' tool."
                    "- Speak the response out loud using the 'speak' tool."
                    "If multiple actions are required, chain them together in the order they should be executed. "
                    "For example, if asked to translate a text and then search for it on Wikipedia and speak the result, first use 'dorfer', then 'fetch_wikipedia_content', and finally 'speak'."
                    ),
                }
            ]
                    # "You are an assistant that can retrieve Wikipedia articles and translate text using dwarvish. "
                    # "When asked about a topic, you can perform the following actions:"
                    # "- Translate text using the 'dorfer' tool."
                    # "- Search Wikipedia using the 'fetch_wikipedia_content' tool."
                    # "If multiple actions are required, chain them together in the order they should be executed. "
                    # "For example, if asked to translate a text and then search for it on Wikipedia, first use 'dorfer' and then 'fetch_wikipedia_content'."
    # = [
        # {
            # "role": "system",
            # "content": (
                # "You are an assistant that can retrieve Wikipedia articles. "
                # "When asked about a topic, you can retrieve Wikipedia articles "
                # "and cite information from them."
                # "You also have a powerful dorfer tool that can translate any response to dwarvish."
                # "Which you can use to translate the final response to dwarvish."
                # "You are allowed to use multiple tools in a single response."
                # "And you do so whenever appropriate."
            # ),
        # }
    # ]

    print(
            "Assistant: "
            "Hi! I can access Wikipedia to help answer your questions about history, "
            "science, people, places, or concepts - or we can just chat about "
            "anything else!"
            )
    print("(Type 'quit' to exit)")

    while True:
        user_input = input("\nYou: ").strip()
        if user_input.lower() == "quit":
            break

        messages.append({"role": "user", "content": user_input})
        assistant_response = handle_tool_calls(client, messages, MODEL, TOOLS)
        messages.append({"role": "assistant", "content": assistant_response})

if __name__ == "__main__":
    chat_loop()


