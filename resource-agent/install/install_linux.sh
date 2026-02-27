#!/bin/bash
# Install Resource Agent as a systemd service on Linux

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_NAME="resource-agent"
SERVICE_USER="${USER}"

echo "=== Resource Agent Linux Installer ==="
echo "Agent directory: $AGENT_DIR"
echo "Service user: $SERVICE_USER"
echo ""

# Check if running as root for service installation
if [ "$EUID" -ne 0 ]; then
    echo "Note: Run with sudo to install systemd service"
    echo "For now, just setting up the virtual environment..."
    INSTALL_SERVICE=false
else
    INSTALL_SERVICE=true
fi

# Create virtual environment if it doesn't exist
if [ ! -d "$AGENT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$AGENT_DIR/venv"
fi

# Activate and install dependencies
echo "Installing dependencies..."
source "$AGENT_DIR/venv/bin/activate"
pip install --upgrade pip
pip install -r "$AGENT_DIR/requirements.txt"

# Create work directory
mkdir -p "$AGENT_DIR/work"

# Create config from example if not exists
if [ ! -f "$AGENT_DIR/config.yaml" ]; then
    echo "Creating config.yaml from example..."
    cp "$AGENT_DIR/config.example.yaml" "$AGENT_DIR/config.yaml"
    echo ""
    echo "⚠️  IMPORTANT: Edit config.yaml with your broker URL and API key!"
    echo "    nano $AGENT_DIR/config.yaml"
fi

if [ "$INSTALL_SERVICE" = true ]; then
    echo ""
    echo "Installing systemd service..."
    
    # Create systemd service file
    cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Money Agents Resource Agent
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$AGENT_DIR
ExecStart=$AGENT_DIR/venv/bin/python $AGENT_DIR/agent.py
Restart=always
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

# Environment
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

    # Reload systemd
    systemctl daemon-reload
    
    echo ""
    echo "=== Installation Complete ==="
    echo ""
    echo "Service commands:"
    echo "  Start:   sudo systemctl start $SERVICE_NAME"
    echo "  Stop:    sudo systemctl stop $SERVICE_NAME"
    echo "  Status:  sudo systemctl status $SERVICE_NAME"
    echo "  Logs:    journalctl -u $SERVICE_NAME -f"
    echo "  Enable:  sudo systemctl enable $SERVICE_NAME  (start on boot)"
    echo ""
    echo "Before starting, edit config.yaml with your broker URL and API key!"
else
    echo ""
    echo "=== Setup Complete ==="
    echo ""
    echo "To run the agent manually:"
    echo "  cd $AGENT_DIR"
    echo "  source venv/bin/activate"
    echo "  python agent.py"
    echo ""
    echo "To install as a system service, run this script with sudo."
fi
