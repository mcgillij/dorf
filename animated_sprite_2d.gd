extends AnimatedSprite2D


func _input(event):
	if event.is_action_pressed("click"):
		EventBus.dorf_clicked.emit()
