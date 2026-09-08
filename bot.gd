extends Control

@export var http: AwaitableHTTPRequest
# Dedicated node for the 0.5s avatar-state poll: sharing one HTTPRequest with
# query requests made sends fail with ERR_BUSY whenever a poll was mid-flight.
@export var state_http: AwaitableHTTPRequest

@onready var dorf: AnimatedSprite2D = %dorf
@onready var timer: Timer = $Timer

var window_out := false
var fastapi_endpoint := ""
var api_token := ""
var dorf_idle := load("res://assets/images/dorf.png")
var dorf_talking := load("res://assets/images/dorf_talking.png")
var dorf_thinking := load("res://assets/images/dorf_thinking.png")

var time_accumulator: float = 0.0
# Last known avatar state; kept on failed polls so a transient api outage
# doesn't snap the pet back to idle.
var current_state := "idle"

# Local pet speech: after a text response lands, the pet polls the api for
# the tts wav the response worker synthesized (source "godot" → pet_tts:{uid}).
const TTS_POLL_INTERVAL_S := 2.0
const TTS_BUDGET_S := 30.0  # synthesis takes seconds; give it room

var _tts_player: AudioStreamPlayer
# Bumps per fetch; a newer query invalidates an older poll loop.
var _tts_fetch_seq := 0

func _ready() -> void:
	# Endpoint/token come from the environment so the pet can point at a
	# remote api (exported builds no longer hardcode localhost). :8100 —
	# :8000 is taken by the godot-ai MCP server in this environment.
	fastapi_endpoint = OS.get_environment("DORF_API_URL")
	if fastapi_endpoint.is_empty():
		fastapi_endpoint = "http://localhost:8100"
	fastapi_endpoint = fastapi_endpoint.trim_suffix("/")
	api_token = OS.get_environment("DORF_API_TOKEN")
	# Speech player for pet queries — built in code, no scene edit needed;
	# reuse swaps .stream and plays again.
	_tts_player = AudioStreamPlayer.new()
	add_child(_tts_player)
	connect_signals()
	$Timer.timeout.connect(back_to_idle)
	$Timer.start()

func _wait_http_ready(node: AwaitableHTTPRequest, timeout_s := 2.0) -> bool:
	# The addon rejects overlapping requests on the same node (ERR_BUSY);
	# wait briefly instead of failing the user's send.
	var deadline := Time.get_ticks_msec() + int(timeout_s * 1000.0)
	while node.is_requesting:
		if Time.get_ticks_msec() >= deadline:
			return false
		await get_tree().process_frame
	return true

func _poll_state() -> void:
	# The poll just skips a tick when its own node is busy.
	if state_http.is_requesting:
		return
	var resp := await state_http.async_request(
		fastapi_endpoint + "/api/avatar_state", _api_headers()
	)
	if resp.success() and resp.status_ok():
		var body: Variant = resp.body_as_json()
		if body is Dictionary and body.has("state"):
			current_state = str(body["state"])
	state_machine(current_state)

func state_machine(state: String) -> void:
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
		_poll_state()

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
	# Surface the answer in the pet's window — it used to be write-only.
	$TextInputWindow.show_response(str(text_response["response"]))
	_fetch_and_play_tts(unique_id)  # fire-and-forget: text never blocks on audio
	dorf.play(&"talking")
	$Timer.start()  # Restart the timer for next poll

func _sleep_s(seconds: float) -> void:
	await get_tree().create_timer(seconds).timeout

func _fetch_and_play_tts(unique_id: String) -> void:
	# Poll GET /api/tts/{uid} until the response worker's synth lands
	# (404 while pending), then play it locally. Text is already on screen;
	# a dead TTS backend just means silence — never an error surfaced.
	_tts_fetch_seq += 1
	var seq := _tts_fetch_seq
	var url := fastapi_endpoint + "/api/tts/" + unique_id
	var deadline_ms := Time.get_ticks_msec() + int(TTS_BUDGET_S * 1000.0)
	while Time.get_ticks_msec() < deadline_ms:
		if seq != _tts_fetch_seq:
			return  # a newer query owns the fetch channel
		# Share the query http node (state polls have their own); wait out
		# an in-flight request instead of failing the poll.
		if not await _wait_http_ready(http, 2.0):
			await _sleep_s(TTS_POLL_INTERVAL_S)
			continue
		var resp := await http.async_request(url, _api_headers())
		if resp.success() and resp.status_ok():
			_play_tts_bytes(resp.bytes)
			return
		await _sleep_s(TTS_POLL_INTERVAL_S)

func _play_tts_bytes(bytes: PackedByteArray) -> void:
	# PCM_16 wav from the provider contract; the buffer parse reads the
	# RIFF header (mix rate / channels) itself.
	if bytes.is_empty():
		return
	var stream := AudioStreamWAV.load_from_buffer(bytes)
	if stream == null:
		return
	_tts_player.stream = stream
	_tts_player.play()

func do_http_get_unique_id(unique_id: String) -> Dictionary:
	if not await _wait_http_ready(http):
		_show_send_error("dorf api: request channel busy, try again")
		return {}
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
	if not await _wait_http_ready(http):
		return {}
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
