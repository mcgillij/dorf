# Build / run tasks for the DORF Godot desktop pet.
# The bot stack lives in client/ (see client/Justfile).

set shell := ["bash", "-uc"]

# Must match export_presets.cfg preset.0.name
preset := "Linux"
export_path := "dist/dorf.x86_64"

# List all tasks
@default:
    just --list

# Export the desktop pet headlessly to ./dist (preset "Linux").
# Needs the dorf api stack up (cd client && just up) to be useful.
@export:
    mkdir -p dist
    godot --headless --export-release {{preset}} {{export_path}}
    @echo "exported: {{export_path}} (+ console wrapper dorf.sh)"

# Remove build artifacts; `just export` regenerates everything.
@clean:
    rm -rf dist
    @echo "cleaned dist/"

# Run the exported pet directly
@run binary="dist/dorf.x86_64":
    chmod +x {{binary}}
    {{binary}}

# --- stack delegation (the pet needs the dorf api on :8100) -----------------

# Start the bot stack (redis + whisper + worker + bot + api)
@up:
    cd client && ./startup

# Stop the bot stack
@down:
    cd client && ./kill

# Sanity-check the stack (redis/whisper/model/bot/worker/api)
@check:
    cd client && just check
