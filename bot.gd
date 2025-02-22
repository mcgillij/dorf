extends Control

@export var http: AwaitableHTTPRequest
@export var input_window: PackedScene

@onready var dorf: AnimatedSprite2D = %dorf
@onready var timer: Timer = $Timer

var window_out := false
var thread: Thread
var fastapi_endpoint := "http://localhost:8000"
var dorf_idle := load("res://assets/images/dorf.png")
var dorf_talking := load("res://assets/images/dorf_talking.png")
var dorf_thinking := load("res://assets/images/dorf_thinking.png")

var time_since_last_poll = 0.0

func _ready() -> void:
	connect_signals()
	$Timer.timeout.connect(back_to_idle)
	$Timer.start()

func _process(delta: float) -> void:
	pass

func back_to_idle():
	dorf.play(&"idle")

func connect_signals() -> void:
	EventBus.input_window_send.connect(_on_input_window_send)
	EventBus.input_window_toggle.connect(toggle_window)
	EventBus.dorf_clicked.connect(dorf_clicked)

func _on_input_window_send(query: String) -> void:
	dorf.play(&"thinking")
	var unique_id := await do_http_query(query)
	print_debug(unique_id)
	var text_response := await do_http_get_unique_id(unique_id["unique_id"])
	dorf.play(&"talking")
	$Timer.start()  # Restart the timer for next poll
	print_debug(text_response)

func do_http_get_unique_id(unique_id: String) -> Dictionary:
	var data = {
		"unique_id": unique_id
	}
	var json_data = JSON.stringify(data)
	var headers = [
		"Content-Type: application/json"
	]
	# Make the POST request with the JSON data
	var resp := await http.async_request(
		fastapi_endpoint + "/api/fetch_response",
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
#		print("Response body:", resp.body)
		return {}

func do_http_query(query: String) -> Dictionary:
	var data = {
		"query": query
	}
	var json_data = JSON.stringify(data)
	var headers = [
		"Content-Type: application/json"
	]
	# Make the POST request with the JSON data
	var resp := await http.async_request(
		fastapi_endpoint + "/api/process_query",
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
		#print("Response body:", resp.body)
		return {}

func toggle_window() -> void:
	var dorf_pos = get_window().position
	$TextInputWindow.position = dorf_pos + Vector2i(-600, 0)
	$TextInputWindow.visible = not $TextInputWindow.visible

func dorf_clicked() -> void:
	toggle_window()

func _on_awaitable_http_request_request_completed(result: int, response_code: int, headers: PackedStringArray, body: PackedByteArray) -> Dictionary:
	var string = body.get_string_from_ascii()
	var json_result = JSON.parse_string(string)
	print(json_result)
	return json_result

func _exit_tree():
	thread.wait_to_finish()
