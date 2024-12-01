extends Control

@export var http: AwaitableHTTPRequest
@export var input_window: PackedScene

@onready var dorf: AnimatedSprite2D = %dorf
@onready var timer: Timer = $Timer
var window_out := false
var thread: Thread
enum DORF_STATE { IDLE, TALKING, THINKING }

var current_state :DORF_STATE = DORF_STATE.IDLE

var dorf_idle := load("res://assets/images/dorf.png")
var dorf_talking := load("res://assets/images/dorf_talking.png")
var dorf_thinking := load("res://assets/images/dorf_thinking.png")

func _ready() -> void:
	connect_signals()

func _process(delta: float) -> void:
	if thread and thread.is_alive() == false:
		toggle_state(DORF_STATE.IDLE)


func connect_signals() -> void:
	EventBus.input_window_send.connect(_on_input_window_send)
	EventBus.input_window_toggle.connect(toggle_window)
	EventBus.dorf_clicked.connect(dorf_clicked)
	http.request_finished.connect(print_stuff)

func print_stuff(results: Dictionary) -> void:
	print_debug(results)

func _on_input_window_send(query: String) -> void:
	toggle_state(DORF_STATE.THINKING)
	#var results := await do_http(query)
	await do_http(query)

	# now send to tts, write python api for this probably unless godot can shell out to local app
	#print(results)
	#toggle_state(DORF_STATE.TALKING)
	#run_system_command(extract_content(results))

func extract_content(json: Dictionary) -> String:
	# Access the first element of the choices list (index 0)
	var choice = json["choices"][0]
	# Extract the message dictionary from the choice
	var message = choice["message"]
	# Retrieve the content from the message dictionary
	return message["content"]

func toggle_state(state: DORF_STATE) -> void:
	current_state = state
	match current_state:
		DORF_STATE.IDLE:
			dorf.play(&"idle")
		DORF_STATE.TALKING:
			dorf.play(&"talking")
		DORF_STATE.THINKING:
			dorf.play(&"thinking")

func run_system_command(response: String):
	# Construct the curl command as a list of arguments
	var cmd := [
		"echo",
		response + "\" | mimic3 --stdout | aplay\""
	]
	# Use OS.execute to run the command and capture its output
	var output = []

	var result = OS.execute(cmd[0], cmd.slice(1), output)
	if result == 0:
		# send singal to close window etc
		print("command executed successfully")

func do_http(query: String) -> Dictionary:
	# Data to be sent to the API
	var data = {
		#"model": "qwen2.5-coder-14b-instruct",
		"model": "llama-3.1-tulu-3-8b",
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

func toggle_window() -> void:
	var dorf_pos = get_window().position
	$TextInputWindow.position = dorf_pos + Vector2i(-600, 0)
	$TextInputWindow.visible = not $TextInputWindow.visible

func dorf_clicked() -> void:
	toggle_window()

func _on_awaitable_http_request_request_completed(result: int, response_code: int, headers: PackedStringArray, body: PackedByteArray) -> void:
	var string = body.get_string_from_ascii()
	timer.start()
	var json_result = JSON.parse_string(string)
	var result_string := extract_content(json_result)
	thread = Thread.new()
	thread.start(run_system_command.bind(result_string), Thread.PRIORITY_HIGH)

func _exit_tree():
	thread.wait_to_finish()


func _on_timer_timeout() -> void:
	toggle_state(DORF_STATE.TALKING)
