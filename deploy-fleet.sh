#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${HOME}/.config/agent-chat-search/config.toml"

# Extract hosts and SSH settings from config
CONFIG_VALUES=$(python3 -c "
import sys
try:
    from agent_search.config import get_config
    cfg = get_config()
    print(' '.join(cfg.hosts))
    print(cfg.ssh_key)
    print(cfg.ssh_user)
except Exception:
    sys.exit(1)
" 2>/dev/null)

HOSTS=()
SSH_KEY="${HOME}/.ssh/id_ed25519"
SSH_USER="${USER}"

if [ -n "${CONFIG_VALUES}" ]; then
    HOSTS_LINE=$(echo "${CONFIG_VALUES}" | sed -n '1p')
    CFG_KEY=$(echo "${CONFIG_VALUES}" | sed -n '2p')
    CFG_USER=$(echo "${CONFIG_VALUES}" | sed -n '3p')
    if [ -n "${HOSTS_LINE}" ]; then
        read -r -a HOSTS <<< "${HOSTS_LINE}"
    fi
    if [ -n "${CFG_KEY}" ]; then
        SSH_KEY="${CFG_KEY}"
    fi
    if [ -n "${CFG_USER}" ]; then
        SSH_USER="${CFG_USER}"
    fi
fi

if [ ${#HOSTS[@]} -eq 0 ]; then
    echo "Error: No fleet hosts configured."
    echo "Define [fleet].hosts in ~/.config/agent-chat-search/config.toml or pass via AGENT_SEARCH_HOSTS."
    exit 1
fi

CURRENT_HOST="$(hostname -s 2>/dev/null || cat /etc/hostname 2>/dev/null || echo '')"

echo "=========================================================="
echo " Deploying updated agent-chat-search across fleet nodes"
echo "=========================================================="
echo "Source: ${SCRIPT_DIR}/agent_search"
echo "Target hosts: ${HOSTS[*]}"
echo ""

for host in "${HOSTS[@]}"; do
    if [ "$host" = "$CURRENT_HOST" ]; then
        echo "[$host] (local machine) Skipping remote push."
        continue
    fi

    echo "[$host] Connecting over Tailscale..."
    SSH_OPTS="ssh -i ${SSH_KEY} -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new"

    # Verify reachability
    if ! ${SSH_OPTS} "${SSH_USER}@${host}" "true" 2>/dev/null; then
        echo "  ⚠ Warning: ${host} is unreachable or timed out. Skipping."
        continue
    fi

    # Ensure remote directory exists
    ${SSH_OPTS} "${SSH_USER}@${host}" "mkdir -p ~/src/agent-chat-search ~/.config/agent-chat-search ~/.local/bin"

    # Rsync package files
    echo "  Syncing agent_search package..."
    rsync -e "${SSH_OPTS}" -aqz --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        "${SCRIPT_DIR}/agent_search/" \
        "${SSH_USER}@${host}:~/src/agent-chat-search/agent_search/"

    # Sync setup.py and install.sh
    rsync -e "${SSH_OPTS}" -aqz \
        "${SCRIPT_DIR}/setup.py" \
        "${SCRIPT_DIR}/install.sh" \
        "${SSH_USER}@${host}:~/src/agent-chat-search/"

    # Sync config.toml if present locally
    if [ -f "${CONFIG_FILE}" ]; then
        rsync -e "${SSH_OPTS}" -aqz \
            "${CONFIG_FILE}" \
            "${SSH_USER}@${host}:~/.config/agent-chat-search/config.toml"
    fi

    # Update ~/.local/bin/agent-search launcher
    ${SSH_OPTS} "${SSH_USER}@${host}" "bash -c '
cat << \"EOF\" > ~/.local/bin/agent-search
#!/usr/bin/env bash
export PYTHONPATH=\"\$HOME/src/agent-chat-search:\${PYTHONPATH}\"
exec python3 -m agent_search.cli \"\$@\"
EOF
chmod +x ~/.local/bin/agent-search
'"

    # Restart central service if it is running on this node
    RESTARTED=$(${SSH_OPTS} "${SSH_USER}@${host}" "
if systemctl --user is-active agent-chat-search >/dev/null 2>&1; then
    systemctl --user restart agent-chat-search
    echo 'restarted'
else
    echo 'inactive'
fi
")
    if [ "$RESTARTED" = "restarted" ]; then
        echo "  ✓ Restarted central agent-chat-search.service"
    fi

    echo "  ✓ $host updated successfully."
    echo ""
done

echo "✓ All live fleet nodes updated."
