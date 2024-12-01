extends Window

@onready var text_input: TextEdit = %text_input

func _ready() -> void:
	EventBus.input_window_toggle.connect(toggle_window)

func toggle_window() -> void:
	text_input.text = ""
	visible = false

func _on_button_pressed() -> void:
	EventBus.input_window_send.emit(text_input.text)
	toggle_window()
