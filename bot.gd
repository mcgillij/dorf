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
	do_http(query)

func toggle_dorf_image() -> void:
	dorf.texture_normal = dorf_talking


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
#func do_http(query: String) -> Dictionary:
	#var resp := await http.async_request("http://0.0.0.0:1234/v1/chat/completions")
	#if resp.success() and resp.status_ok():
		#print(resp.status)                   # 200
		#print(resp.headers["content-type"])  # application/json
		#var json: Dictionary
		#json = resp.body_as_json()
		#return json
	#return {}

func show_window() -> void:
	$TextInputWindow.visible = true


func _on_texture_rect_pressed() -> void:
	toggle_dorf_image()
	show_window()
