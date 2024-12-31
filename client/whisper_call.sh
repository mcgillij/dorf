curl --location --request POST http://127.0.0.1:8080/inference --form file=@"./output.wav" --form temperature="0.2" --form response-format="text" --form audio_format=wav
