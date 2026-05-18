# Makefile
SHELL := /bin/bash

# --- Config you can tweak ---
PARTITION   ?= gpu_intr
GRES        ?= gpu:1
CPUS        ?= 8
TIME        ?= 10:00:00
LOGIN_HOST  ?= cc21dev1          # host you SSH into from your laptop
LOCAL_PORT  ?= 30000             # port on your laptop
APP_PORT    ?= 8080              # uvicorn port on the cluster node
APP_MODULE  ?= app.main:app
APP_ARGS    ?= --reload --host 0.0.0.0 --port $(APP_PORT)
PIP         ?= pip               # customize if needed (e.g., pip3 or .venv/bin/pip)

# Detect the *current* node short hostname (evaluated when each recipe runs)
HOST_SHORT  := $(shell hostname -s 2>/dev/null || echo unknown)

# --- Dependency management (idempotent) ---
DEPS_STAMP := .deps.stamp

$(DEPS_STAMP): requirements.txt
	$(PIP) install -r requirements.txt
	@date > $@

.PHONY: help alloc tunnel serve start deps-clean

help:
	@echo "Targets:"
	@echo "  make alloc      - allocate a GPU node (interactive shell via srun)"
	@echo "  make tunnel     - print the SSH tunnel command (copy/paste on your laptop)"
	@echo "  make serve      - install deps if needed, then run uvicorn on the node"
	@echo "  make start      - print tunnel hint, then run uvicorn"
	@echo "  make deps-clean - force reinstall on next serve (removes stamp)"
	@echo ""
	@echo "Typical flow:"
	@echo "  1) On the cluster login node:   make alloc"
	@echo "     (you drop into an interactive shell on a GPU node)"
	@echo "  2) On that GPU node shell:      make tunnel  # shows the command to run on your laptop"
	@echo "  3) On the GPU node shell:       make serve   # starts uvicorn"
	@echo "  4) On your laptop: paste & run the SSH command from step 2"

# 1) Allocate GPU node and drop into an interactive login shell on it
alloc:
	srun -p $(PARTITION) --gres=$(GRES) -c $(CPUS) --time=$(TIME) --pty bash -l

# 2 & 3) Print the SSH tunnel command with the actual compute node name
#        (Run this *on the allocated node*; copy/paste the output on your laptop)
tunnel:
	@echo ""
	@echo "=== Copy & paste this in a terminal on YOUR LAPTOP ==="
	@echo "ssh -N -L $(LOCAL_PORT):$(HOST_SHORT):$(APP_PORT) $(LOGIN_HOST)"
	@echo "======================================================"
	@echo ""
	@echo "(Detected node: $(HOST_SHORT))"

# 4) Start uvicorn on the compute node
serve: $(DEPS_STAMP)
	uvicorn $(APP_MODULE) $(APP_ARGS)

# Convenience: print tunnel hint then start server
start: tunnel serve

# Force a reinstall next time (use if you changed system Python, etc.)
deps-clean:
	@rm -f $(DEPS_STAMP)
	@echo "Removed $(DEPS_STAMP). Next 'make serve' will reinstall requirements."