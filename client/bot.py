import os
import discord
from discord.ext import commands, tasks
import requests
import json

# Replace these with your actual values
AUTH_TOKEN = os.getenv('AUTH_TOKEN', "")
WORKSPACE = "a-new-workspace"
SESSION_ID = "randomid"  # Session ID can be static or dynamically generated

INTENTS = discord.Intents.default()
INTENTS.message_content = True
# Initialize the bot with a command prefix (e.g., !)
bot = commands.Bot(command_prefix='!', intents=INTENTS)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

@bot.command(name='derf')
async def derf(ctx, *, message: str):
    try:
        # Send a confirmation message to the channel
#        await ctx.send("Your request has been received.")
        
        # Query the LLM
        text_response = get_response(message, SESSION_ID, WORKSPACE, AUTH_TOKEN)
        
        if text_response:
            await ctx.send(f"{text_response}")
        else:
            await ctx.send("Failed to receive a response from the Derf.")
    except Exception as e:
        print(f"Error handling command '!derf': {e}")
        await ctx.send("An error occurred while processing your request. Please try again later.")

def get_response(message, session_id, workspace, auth_token):
    url = f"http://localhost:3001/api/v1/workspace/{workspace}/chat"
    
    headers = {
        'accept': 'application/json',
        'Authorization': f'Bearer {auth_token}',
        'Content-Type': 'application/json'
    }
    
    data = {
        "message": message,
        "mode": "chat",
        "sessionId": session_id,
        "attachments": []
    }
    
    print(f"Sending POST request to {url} with headers: {headers} and data: {data}")
    response = requests.post(url, headers=headers, json=data)
    
    if response.status_code == 200:
        response_data = response.json()
        text_response = response_data.get('textResponse', 'No text response found.')
        print(f"Received valid response from LLM: {text_response[:100]}...")
        return text_response
    else:
        print(f"Failed to get a valid response. Status code: {response.status_code}, Response body: {response.text}")
        return None

# Replace 'your_bot_token_here' with your actual Discord bot token
bot.run(os.getenv('DISCORD_BOT_TOKEN', ""))

