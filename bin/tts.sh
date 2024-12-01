#!/bin/bash

mkdir -p "${HOME}/.local/share/mycroft/mimic3"
chmod a+rwx "${HOME}/.local/share/mycroft/mimic3"
docker run \
       -d \
       -it \
       -p 59125:59125 \
       -v "${HOME}/.local/share/mycroft/mimic3:/home/mimic3/.local/share/mycroft/mimic3" \
       'mycroftai/mimic3'
