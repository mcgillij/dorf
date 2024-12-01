extends Window


@onready var text_input: TextEdit = %text_input

func _on_button_pressed() -> void:
	EventBus.input_window_send.emit(text_input.text)
