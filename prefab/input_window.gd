extends Window

@onready var text_input: TextEdit = %text_input
@onready var response: TextEdit = %response

func _on_button_pressed() -> void:
	EventBus.input_window_send.emit(text_input.text)
	text_input.text = ""
	visible = false

func show_response(text: String) -> void:
	# The pet's window used to be write-only; answers now appear here.
	response.text = text
	text_input.text = ""
	visible = true
