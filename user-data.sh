#!/bin/bash
# EC2 user-data for Amazon Linux 2023, free-tier t2.micro.
# Launches the app with Docker in one boot. Paste into EC2 > Advanced > User data.
# After launch: http://<EC2-PUBLIC-IP>:8000
set -xe
dnf update -y
dnf install -y docker git
systemctl enable --now docker
usermod -aG docker ec2-user

# App source: replace with your repo URL, or scp this project to /opt/app instead.
APP_DIR=/opt/card-extractor
if [ ! -d "$APP_DIR" ]; then
  # If GIT_REPO is set, clone it; otherwise expect files at /opt/card-extractor already.
  if [ -n "$GIT_REPO" ]; then
    git clone "$GIT_REPO" "$APP_DIR"
  else
    mkdir -p "$APP_DIR"
  fi
fi
cd "$APP_DIR"

# API key for Qwen (set one of these in EC2 console or edit here)
# export QWEN_PROVIDER=dashscope
# export DASHSCOPE_API_KEY=sk-your-key
# export QWEN_PROVIDER=openrouter
# export OPENROUTER_API_KEY=sk-or-your-key
# export QWEN_PROVIDER=groq
# export GROQ_API_KEY=gsk-your-key
if [ -f .env ]; then
  set -a; source .env; set +a
fi

# Build + run only if project files are already present (otherwise upload
# code first, then run the same two docker commands below over SSH).
if [ -f Dockerfile ]; then
  docker build -t card-extractor .
  docker run -d --restart always --name card-extractor -p 80:8000 \
    -e QWEN_PROVIDER="${QWEN_PROVIDER:-mock}" \
    -e DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}" \
    -e OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}" \
    -e HF_TOKEN="${HF_TOKEN:-}" \
    -e GROQ_API_KEY="${GROQ_API_KEY:-}" \
    -e QWEN_MODEL="${QWEN_MODEL:-}" \
    card-extractor
fi
