#!/bin/bash
cd ~/task-tracker
python3 app.py &
sleep 1.5
open http://localhost:5000
