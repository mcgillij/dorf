extends Control

@export var http: AwaitableHTTPRequest
@export var input_window: PackedScene
@onready var dorf: TextureButton = %DORF

enum DORF_STATE { IDLE, TALKING, THINKING }

var dorf_idle := load("res://assets/images/dorf.png")
var dorf_talking := load("res://assets/images/dorf_talking.png")

func _ready() -> void:
	connect_signals()

func connect_signals() -> void:
	EventBus.input_window_send.connect(_on_input_window_send)

func _on_input_window_send(query: String) -> void:
	var results := await do_http(query)
	# now send to tts, write python api for this probably unless godot can shell out to local app
	print(results)
	run_system_command(extract_content(results))


func extract_content(json: Dictionary) -> String:
	# Access the first element of the choices list (index 0)
	var choice = json["choices"][0]
	# Extract the message dictionary from the choice
	var message = choice["message"]
	# Retrieve the content from the message dictionary
	return message["content"]

func toggle_dorf_image() -> void:
	dorf.texture_normal = dorf_talking

func run_system_command(response: String):
	print_debug(response)
	# Construct the curl command as a list of arguments
	var cmd := [
		"echo",
		response + "\" | mimic3 --stdout | aplay\""
		#"|",
		#"mimic3",
		#"--stdout",
		#"|",
		#"aplay"
	]
	#var cmd = [
		#"curl",
		#"-X", "POST",
		#"--data", "'" + response + "'",
		#"--output", "-",
		#"localhost:59125/api/tts", "|",
		#"aplay",
		#"-",
		#"&"
	#]

	# Use OS.execute to run the command and capture its output
	var output = []
	print_debug(cmd[0])
	print_debug(cmd.slice(1))
	var result = OS.execute(cmd[0], cmd.slice(1), output)
	print(result)
	print("did I get past the execute?")
	print_debug(output)
	if result == 0:
		print("Curl command executed successfully")

func do_http(query: String) -> Dictionary:
	# Data to be sent to the API
	var data = {
		"model": "qwen2.5-coder-14b-instruct",
		"messages": [{"role": "user", "content": query}],
		"temperature": 0.7
	}

	# Convert the data to JSON string
	var json_data = JSON.stringify(data)

	# Define the request headers
	var headers = [
		"Content-Type: application/json"
	]


	# Make the POST request with the JSON data
	var resp := await http.async_request(
		"http://192.168.2.35:1234/v1/chat/completions",
		headers,
		HTTPClient.METHOD_POST,
		json_data
	)

	if resp.success() and resp.status_ok():
		print(resp.status)                   # 200
		print(resp.headers["content-type"])  # application/json
		var response_json: Dictionary
		response_json = resp.body_as_json()
		return response_json
	else:
		print("Request failed")
		print("Status:", resp.status)
		print("Response body:", resp.body)
		return {}

func show_window() -> void:
	$TextInputWindow.visible = true

func _on_texture_rect_pressed() -> void:
	toggle_dorf_image()
	show_window()
