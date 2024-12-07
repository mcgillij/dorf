from fastapi import FastAPI, HTTPException, Request
import json
import shutil
import sys
import threading
import subprocess
import time
import urllib.parse
import urllib.request
from random import randint
from openai import OpenAI

# Initialize client
client = OpenAI(base_url="http://0.0.0.0:1234/v1", api_key="lm-studio")
MODEL = "qwen2.5-7b-instruct"

# Define tools
SUMMARIZE_TOOL = {
    "type": "function",
    "function": {
        "name": "summarizer",
        "description": (
            "Request the LLM to summarize the provided response."
            "Always use this if you need a concise version of a longer text."
            "This can be useful when the user asks for a brief summary or a quick overview."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "response_text": {
                    "type": "string",
                    "description": "The response text to be summarized.",
                },
            },
            "required": ["response_text"],
        },
    },
}

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

TOOLS = [WIKI_TOOL, DORF_TOOL, SUMMARIZE_TOOL]

app = FastAPI()

# Define tool functions

def summarizer(message: str) -> str:
    # Add the text to be summarized to the messages
    summarizer_messages = [
        {
            "role": "system",
            "content": (
                "You are a summarizer and you are very good at extracting succinct information from longer texts."
            ),
        }
    ]
    summarizer_messages.append({
        "role": "user",
        "content": f"Please summarize the following text: {message}"
    })

    # Create a chat completion request to the LLM with the summary prompt
    response = client.chat.completions.create(
        model=MODEL,
        messages=summarizer_messages,
        max_tokens=randint(150, 250),  # Adjust as needed for desired length of summary
        n=1,
        stop=None,
        temperature=0.7,
    )

    # Extract and return the summarized text from the response
    summary = response.choices[0].message.content.strip()
    return summary

def dorfer(llm_response: str) -> dict:
    # Implement your dwarvish translation logic here
    translated_text = llm_response  # Placeholder for actual translation
    return {"status": "success", "translated_text": translated_text}


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

        result = {
            "status": "success",
            "title": normalized_title,
            "content": pages[page_id].get("extract", ""),
        }
        return result

    except Exception as e:
        print(f"Error fetching Wikipedia content: {e}")
        return {"status": "error", "message": f"An error occurred while fetching Wikipedia content: {str(e)}"}

@app.post("/speak/")
async def speak(content: str) -> dict:
    print(f"SPEEEEK called with: {content}")
    # Construct the TTS command
    command = f'echo "{content}" | mimic3 --stdout | aplay'
    try:
        # Execute the command using subprocess
        subprocess.run(command, check=True, shell=True)
    except subprocess.CalledProcessError as e:
        print(f"Error executing TTS: {e}")
        return {"status": "error", "message": f"An error occurred while executing TTS: {str(e)}"}
    return {"status": "success", "message": "Spoke successfully"}

@app.post("/chat/")
async def chat(user_message: str) -> dict:
    try:
        if not user_message:
            raise HTTPException(status_code=400, detail="No content provided")

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an assistant that can retrieve Wikipedia articles, translate text using dwarvish, and help summarize text."
                    "When asked about a topic, you can perform the following actions:"
                    "- Search Wikipedia using the 'fetch_wikipedia_content' tool."
                    "- Translate text using the 'dorfer' tool."
                    "- Summarize text using the 'summarizer' tool."
                    "If multiple actions are required, chain them together in the order they should be executed. "
                    "For example, if asked to translate a text and then search for it on Wikipedia summarize it, first use 'fetch_wikipedia_content', and finally 'summarize' the results before responding."
                ),
            }
        ]

        messages.append({"role": "user", "content": user_message})
        assistant_response = handle_tool_calls(client, messages, MODEL, TOOLS)
        messages.append({"role": "assistant", "content": assistant_response})
        await speak(assistant_response)
        return {"response": assistant_response}

    except Exception as e:
        print(f"Error processing chat request: {e}")
        raise HTTPException(status_code=500, detail=f"An error occurred while processing the chat request: {str(e)}")

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
                elif tool_call.function.name == "summarizer":
                    args = json.loads(tool_call.function.arguments)
                    result = summarizer(args["response_text"])
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

@app.post("/reset/")
async def reset_chat():
    global messages
    messages = [
        {
            "role": "system",
            "content": (
                "You are an assistant that can retrieve Wikipedia articles, translate text using dwarvish."
                "When asked about a topic, you can perform the following actions:"
                "- Translate text using the 'dorfer' tool."
                "- Search Wikipedia using the 'fetch_wikipedia_content' tool."
                "If multiple actions are required, chain them together in the order they should be executed. "
                "For example, if asked to translate a text and then search for it on Wikipedia, first use 'dorfer', then 'fetch_wikipedia_content'."
            ),
        }
    ]
    return {"message": "Chat session reset"}

@app.get("/messages/")
async def get_messages():
    return messages

# Initialize the messages list
messages = [
    {
        "role": "system",
        "content": (
            "You are an assistant that can retrieve Wikipedia articles, translate text using dwarvish."
            "When asked about a topic, you can perform the following actions:"
            "- Translate text using the 'dorfer' tool."
            "- Search Wikipedia using the 'fetch_wikipedia_content' tool."
            "If multiple actions are required, chain them together in the order they should be executed. "
            "For example, if asked to translate a text and then search for it on Wikipedia, first use 'dorfer', then 'fetch_wikipedia_content'."
        ),
    }
]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

