#!/usr/bin/env bash
set -e

INSTALL_DIR="${HOME}/.local/share/agent-chat-search-app"
BIN_DIR="${HOME}/.local/bin"

echo "Installing agent-chat-search..."

mkdir -p "${INSTALL_DIR}" "${BIN_DIR}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -r "${SCRIPT_DIR}/agent_search" "${INSTALL_DIR}/"

cat << 'EOF' > "${BIN_DIR}/agent-search"
#!/usr/bin/env bash
export PYTHONPATH="${HOME}/.local/share/agent-chat-search-app:${PYTHONPATH}"
exec python3 -m agent_search.cli "$@"
EOF

chmod +x "${BIN_DIR}/agent-search"

echo "✓ agent-chat-search installed to ${BIN_DIR}/agent-search"
echo ""
echo "Try running:"
echo "  agent-search stats"
echo "  agent-search \"your search query\""
