#!/bin/bash

SESSION="sttt_server"

# Tạo session mới (chạy nền)
tmux new-session -d -s $SESSION

# ===== API SERVER =====
tmux rename-window -t $SESSION "api"
tmux send-keys -t $SESSION "source venv/bin/activate" C-m
tmux send-keys -t $SESSION "uvicorn api_service.main:app --host 0.0.0.0 --port 8111 --reload" C-m

# ===== WHISPER WORKER =====
tmux new-window -t $SESSION -n whisper
tmux send-keys -t $SESSION:whisper "source venv/bin/activate" C-m
tmux send-keys -t $SESSION:whisper "python3 -m worker.whisper_worker" C-m

# ===== NLLB WORKER =====
tmux new-window -t $SESSION -n nllb
tmux send-keys -t $SESSION:nllb "source venv/bin/activate" C-m
tmux send-keys -t $SESSION:nllb "python3 -m worker.nllb_worker" C-m

# Attach vào session
tmux attach -t $SESSION