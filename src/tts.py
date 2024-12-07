import subprocess

def tts(content):
    # Construct the TTS command
    command = f'echo "{content}" | mimic3 --stdout | aplay'
    
    try:
        # Execute the command using subprocess
        subprocess.run(command, check=True, shell=True)
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while executing the TTS command: {e}")

# Example usage
content_to_speak = "Hello, this is a test message."
tts(content_to_speak)

