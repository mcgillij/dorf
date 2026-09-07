extends Control

@export var http: AwaitableHTTPRequest

@onready var dorf: AnimatedSprite2D = %dorf
@onready var timer: Timer = $Timer

var window_out := false
var fastapi_endpoint := ""
var api_token := ""
var dorf_idle := load("res://assets/images/dorf.png")
var dorf_talking := load("res://assets/images/dorf_talking.png")
var dorf_thinking := load("res://assets/images/dorf_thinking.png")

var db: SQLite
var time_accumulator: float = 0.0

func _ready() -> void:
	# Endpoint/token come from the environment so the pet can point at a
	# remote api (exported builds no longer hardcode localhost).
	fastapi_endpoint = OS.get_environment("DORF_API_URL")
	if fastapi_endpoint.is_empty():
		fastapi_endpoint = "http://localhost:8000"
	fastapi_endpoint = fastapi_endpoint.trim_suffix("/")
	api_token = OS.get_environment("DORF_API_TOKEN")
	db = SQLite.new()
	db.path = "res://client/avatar_state.db"
	db.open_db()
	connect_signals()
	$Timer.timeout.connect(back_to_idle)
	$Timer.start()

func get_state() -> String:
	# id DESC mirrors the bot side (statemanager.py): CURRENT_TIMESTAMP has
	# 1s resolution so same-second writes tie arbitrarily under updated_at.
	db.query("SELECT state FROM avatar_state ORDER BY id DESC LIMIT 1")
	var d: Array = db.query_result
	# Empty result = fresh clone / exported PCK (the .db isn't packed).
	# Fail soft to idle instead of erroring every 0.5s in _process.
	if d.is_empty() or d[0] == null or not d[0].has("state"):
		return "idle"
	return str(d[0]["state"])

func state_machine() -> void:
	var state := get_state()
	match state:
		"idle":
			dorf.play(&"idle")
		"thinking":
			dorf.play(&"thinking")
		"talking":
			dorf.play(&"talking")
		# The bot writes "drawing" (sdcog); no dedicated frames yet, so
		# thinking is the closest visual. Unknown states fall back to idle.
		"drawing":
			dorf.play(&"thinking")
		_:
			dorf.play(&"idle")

func _process(delta: float) -> void:
	time_accumulator += delta
	if time_accumulator >= 0.5:
		time_accumulator = 0.0
		state_machine()

func back_to_idle():
	dorf.play(&"idle")

func connect_signals() -> void:
	EventBus.input_window_send.connect(_on_input_window_send)
	EventBus.dorf_clicked.connect(dorf_clicked)

func _api_headers() -> PackedStringArray:
	var headers := PackedStringArray(["Content-Type: application/json"])
	if not api_token.is_empty():
		headers.append("X-Dorf-Token: " + api_token)
	return headers

func _show_send_error(message: String) -> void:
	push_warning(message)
	dorf.play(&"idle")
	$Timer.start()

func _on_input_window_send(query: String) -> void:
	dorf.play(&"thinking")
	var result := await do_http_query(query)
	if result.is_empty() or not result.has("unique_id"):
		# One failed request used to wedge the avatar in "talking" forever.
		_show_send_error("dorf api: process_query failed (is the api running?)")
		return
	var unique_id := str(result["unique_id"])
	var text_response := await do_http_get_unique_id(unique_id)
	if text_response.is_empty() or not text_response.has("response"):
		_show_send_error("dorf api: no response (is the bot process running?)")
		return
	print_debug(text_response)
	dorf.play(&"talking")
	$Timer.start()  # Restart the timer for next poll

func do_http_get_unique_id(unique_id: String) -> Dictionary:
	var data = {
		"unique_id": unique_id
	}
	var json_data = JSON.stringify(data)
	var resp := await http.async_request(
		fastapi_endpoint + "/api/fetch_response",
		_api_headers(),
		HTTPClient.METHOD_POST,
		json_data
	)
	if resp.success() and resp.status_ok():
		return resp.body_as_json()
	print("Request failed")
	print("Status:", resp.status)
	return {}

func do_http_query(query: String) -> Dictionary:
	var data = {
		"query": query
	}
	var json_data = JSON.stringify(data)
	var resp := await http.async_request(
		fastapi_endpoint + "/api/process_query",
		_api_headers(),
		HTTPClient.METHOD_POST,
		json_data
	)
	if resp.success() and resp.status_ok():
		return resp.body_as_json()
	print("Request failed")
	print("Status:", resp.status)
	return {}

func toggle_window() -> void:
	var dorf_pos = get_window().position
	var pos = dorf_pos + Vector2i(-600, 0)
	# Don't spawn the window off-screen when the pet sits near the left edge.
	if pos.x < 0:
		pos.x = 0
	$TextInputWindow.position = pos
	$TextInputWindow.visible = not $TextInputWindow.visible

func dorf_clicked() -> void:
	toggle_window()
