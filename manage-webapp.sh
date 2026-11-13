#!/bin/bash

# TradingAgents Web Application Process Manager
# Manages the Python FastAPI/Uvicorn process on port 8010 (proxied by Apache on 8011)
#
# Usage: ./manage-webapp.sh [start|stop|restart|status|help]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="${SCRIPT_DIR}/.venv"
PYTHON_CMD="${VENV_PATH}/bin/python"
PID_FILE="/tmp/tradingagents-webapp.pid"
LOG_FILE="${SCRIPT_DIR}/logs/webapp.log"

# Process configuration
PYTHON_PORT=8010
APACHE_PORT=8011
NETWORK_IP=$(hostname -I | awk '{print $1}')

# Ensure logs directory exists
mkdir -p "${SCRIPT_DIR}/logs"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check if venv exists
check_venv() {
    if [ ! -d "$VENV_PATH" ]; then
        log_error "Virtual environment not found at $VENV_PATH"
        exit 1
    fi
}

# Get process info
get_process_info() {
    ps aux | grep -E "${PYTHON_CMD}.*webapp" | grep -v grep || echo ""
}

# Start the webapp
start_webapp() {
    log_info "Starting TradingAgents webapp (Python on port $PYTHON_PORT)..."
    
    check_venv
    
    # Check if already running
    if [ -f "$PID_FILE" ]; then
        OLD_PID=$(cat "$PID_FILE")
        if ps -p "$OLD_PID" > /dev/null 2>&1; then
            log_warning "Process already running (PID: $OLD_PID)"
            status_webapp
            return 0
        else
            rm -f "$PID_FILE"
        fi
    fi
    
    # Start the process in background with the correct port
    cd "$SCRIPT_DIR"
    source "${VENV_PATH}/bin/activate"
    
    nohup env TRADINGAGENTS_WEB_PORT=$PYTHON_PORT "${PYTHON_CMD}" -m webapp >> "$LOG_FILE" 2>&1 &
    NEW_PID=$!
    echo "$NEW_PID" > "$PID_FILE"
    
    # Give it a moment to start
    sleep 2
    
    # Check if process is still running
    if ps -p "$NEW_PID" > /dev/null 2>&1; then
        log_success "✅ Webapp started successfully (PID: $NEW_PID)"
        
        # Wait for API to be ready
        log_info "Waiting for API to be ready on port $PYTHON_PORT..."
        for i in {1..30}; do
            if curl -s "http://127.0.0.1:${PYTHON_PORT}/api/health" > /dev/null 2>&1; then
                log_success "✅ API is ready on http://127.0.0.1:${PYTHON_PORT}"
                log_info "Accessible from network at: http://${NETWORK_IP}:${APACHE_PORT}"
                return 0
            fi
            sleep 1
        done
        
        log_warning "API not responding after 30s, but process is running. Check logs: $LOG_FILE"
    else
        log_error "Failed to start webapp. Check logs: $LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# Stop the webapp
stop_webapp() {
    log_info "Stopping TradingAgents webapp..."
    
    if [ ! -f "$PID_FILE" ]; then
        PROC_INFO=$(get_process_info)
        if [ -z "$PROC_INFO" ]; then
            log_warning "No running process found"
            return 0
        fi
        PID=$(echo "$PROC_INFO" | awk '{print $2}')
    else
        PID=$(cat "$PID_FILE")
    fi
    
    if [ -z "$PID" ]; then
        log_warning "Could not determine process ID"
        return 0
    fi
    
    if ps -p "$PID" > /dev/null 2>&1; then
        log_info "Killing process $PID..."
        kill "$PID" 2>/dev/null || true
        
        # Wait up to 10 seconds for graceful shutdown
        for i in {1..10}; do
            if ! ps -p "$PID" > /dev/null 2>&1; then
                log_success "✅ Process stopped successfully"
                rm -f "$PID_FILE"
                return 0
            fi
            sleep 1
        done
        
        # Force kill if still running
        log_warning "Process did not stop gracefully, forcing..."
        kill -9 "$PID" 2>/dev/null || true
        sleep 1
        
        if ps -p "$PID" > /dev/null 2>&1; then
            log_error "Failed to stop process"
            return 1
        else
            log_success "✅ Process force-stopped"
            rm -f "$PID_FILE"
            return 0
        fi
    else
        log_warning "Process is not running"
        rm -f "$PID_FILE"
    fi
}

# Show webapp status
status_webapp() {
    log_info "Checking TradingAgents webapp status..."
    
    # Check Python process
    PROC_INFO=$(get_process_info)
    if [ -n "$PROC_INFO" ]; then
        PID=$(echo "$PROC_INFO" | awk '{print $2}')
        log_success "✅ Python process is running"
        echo -e "   PID: ${GREEN}$PID${NC}"
        
        # Show resource usage
        CPU=$(echo "$PROC_INFO" | awk '{print $3}')
        MEM=$(echo "$PROC_INFO" | awk '{print $4}')
        echo -e "   CPU: ${YELLOW}${CPU}%${NC}"
        echo -e "   MEM: ${YELLOW}${MEM}%${NC}"
    else
        log_error "❌ Python process is NOT running"
    fi
    
    # Check API health
    if HEALTH=$(curl -s "http://127.0.0.1:${PYTHON_PORT}/api/health" 2>/dev/null); then
        VERSION=$(echo "$HEALTH" | grep -o '"version":"[^"]*"' | cut -d'"' -f4)
        STATUS=$(echo "$HEALTH" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
        log_success "✅ API is responding"
        echo -e "   Status: ${GREEN}$STATUS${NC}"
        echo -e "   Version: ${GREEN}$VERSION${NC}"
    else
        log_error "❌ API is not responding on port $PYTHON_PORT"
    fi
    
    # Access points
    echo ""
    log_info "Access points:"
    echo -e "   Local:        ${GREEN}http://127.0.0.1:${PYTHON_PORT}${NC}"
    echo -e "   Network:      ${GREEN}http://${NETWORK_IP}:${APACHE_PORT}${NC}"
    echo -e "   Logs:         ${YELLOW}${LOG_FILE}${NC}"
    echo ""
}

# Restart the webapp
restart_webapp() {
    log_info "Restarting TradingAgents webapp..."
    stop_webapp
    sleep 2
    start_webapp
}

# Show help
show_help() {
    cat << EOF
TradingAgents Web Application Manager

Usage: $0 [COMMAND]

Commands:
    start       Start the webapp
    stop        Stop the webapp
    restart     Restart the webapp (stop then start)
    status      Show status of the webapp
    help        Show this help message

Examples:
    ./manage-webapp.sh start
    ./manage-webapp.sh stop
    ./manage-webapp.sh restart
    ./manage-webapp.sh status

Architecture:
    - Python FastAPI/Uvicorn runs on 127.0.0.1:$PYTHON_PORT
    - Apache2 proxies requests from 192.168.1.x:$APACHE_PORT
    - Access from network: http://${NETWORK_IP}:${APACHE_PORT}

Log file: $LOG_FILE
Apache config: /etc/apache2/sites-available/tradingagents-8010.conf

EOF
}

# Main logic
case "${1:-status}" in
    start)
        start_webapp
        ;;
    stop)
        stop_webapp
        ;;
    restart)
        restart_webapp
        ;;
    status)
        status_webapp
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: $1"
        echo ""
        show_help
        exit 1
        ;;
esac
