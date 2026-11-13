# 🚀 TradingAgents WebApp - Deployment & Management Guide

## 📊 Current Status

```
✅ Application:  TradingAgents v0.8.7+
✅ Python:       3.14.4
✅ Web Server:   Apache2
✅ Framework:    FastAPI/Uvicorn (proxied via Apache)
✅ Port:         8011 (public), 8000 (Python)
✅ Location:     /var/www/TradingAgents
```

## 🎯 Quick Commands

| Action | Command |
|--------|---------|
| **Status** | `./manage-webapp.sh status` |
| **Start** | `./manage-webapp.sh start` |
| **Stop** | `./manage-webapp.sh stop` |
| **Restart** | `./manage-webapp.sh restart` |
| **View Logs** | `tail -f logs/webapp.log` |
| **Help** | `./manage-webapp.sh help` |

## 🌐 Access Points

```
Web Console:     http://localhost:8011
API Endpoint:    http://localhost:8011/api
Health Check:    http://localhost:8011/api/health
API Docs (if):   http://localhost:8011/docs
```

## 🔧 Management Scripts

### Bash Script (Primary)
```bash
cd /var/www/TradingAgents
./manage-webapp.sh [start|stop|restart|status]
```

### Python Script (Alternative)
```bash
cd /var/www/TradingAgents
source .venv/bin/activate
python manage_webapp.py [start|stop|restart|status]
```

## 📋 Directory Structure

```
/var/www/TradingAgents/
├── manage-webapp.sh              # Main management script (BASH)
├── manage_webapp.py              # Alternative management script (Python)
├── DEPLOYMENT_GUIDE.md           # This file
├── WEBAPP_MANAGEMENT.md          # Detailed documentation
├── SCRIPTS_CREATED.md            # Files created summary
├── WEBAPP_QUICK_START.sh         # Quick reference
├── logs/                         # Log directory
│   └── webapp.log               # Application logs
├── .venv/                        # Python virtual environment
├── webapp/                       # Web application code
├── tradingagents/               # Core trading agents
├── cli/                         # Command-line tools
├── tests/                       # Test suite
└── docs/                        # Documentation
```

## 🔍 Monitoring

### Real-time Status
```bash
./manage-webapp.sh status
```

### Continuous Monitoring
```bash
watch -n 5 './manage-webapp.sh status'
```

### Application Logs
```bash
tail -f logs/webapp.log
```

### Apache Logs
```bash
sudo tail -f /var/log/apache2/error.log
sudo tail -f /var/log/apache2/access.log
```

### Process Information
```bash
ps aux | grep 'python -m webapp'
lsof -i :8011
lsof -i :8000
```

## 🔄 Common Operations

### Daily Restart
```bash
./manage-webapp.sh restart
```

### Scheduled Restart (Cron)
```bash
# Edit crontab
crontab -e

# Add line for 2 AM every day
0 2 * * * /var/www/TradingAgents/manage-webapp.sh restart >> /var/www/TradingAgents/logs/cron.log 2>&1

# List cron jobs
crontab -l
```

### Health Check Script
```bash
#!/bin/bash
if ! curl -s http://localhost:8011/api/health > /dev/null; then
    echo "API is down, restarting..."
    /var/www/TradingAgents/manage-webapp.sh restart
fi
```

## 🆘 Troubleshooting

### API Not Responding
```bash
# Check status
./manage-webapp.sh status

# Restart
./manage-webapp.sh restart

# Check logs
tail -50 logs/webapp.log
sudo tail -50 /var/log/apache2/error.log
```

### Port Already in Use
```bash
# Find process using port 8011
lsof -i :8011

# Find process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Restart
./manage-webapp.sh restart
```

### High CPU/Memory Usage
```bash
# Check resource usage
ps aux | grep webapp

# Check what's consuming resources
top -p <PID>

# Restart to reset
./manage-webapp.sh restart

# Review logs for errors
tail -100 logs/webapp.log
```

### Permission Denied
```bash
# Make scripts executable
chmod +x manage-webapp.sh
chmod +x manage_webapp.py

# For sudo operations, check sudoers
sudo visudo
```

## 📈 Performance Optimization

### Increase Resource Limits
```bash
# Check current limits
ulimit -a

# Increase file descriptors
ulimit -n 65536
```

### Monitor Disk Space
```bash
df -h
du -sh /var/www/TradingAgents/

# Rotate logs if needed
logrotate -f /etc/logrotate.d/tradingagents
```

### Database Maintenance (if applicable)
```bash
# Check database size
du -sh /var/www/TradingAgents/data/

# Backup before maintenance
cp -r /var/www/TradingAgents/data /var/www/TradingAgents/data.backup
```

## 🔐 Security Notes

- Application runs on localhost only (default)
- Apache handles external traffic
- No credentials in logs
- Use HTTPS in production (configure Apache)
- Regularly update dependencies

## 📱 Integration Examples

### Python Integration
```python
import subprocess
import json

def get_status():
    result = subprocess.run(
        ["./manage-webapp.sh", "status"],
        cwd="/var/www/TradingAgents",
        capture_output=True,
        text=True
    )
    return result.stdout

def restart_webapp():
    subprocess.run(
        ["./manage-webapp.sh", "restart"],
        cwd="/var/www/TradingAgents"
    )
```

### Node.js Integration
```javascript
const { execSync } = require('child_process');

function getStatus() {
    try {
        const output = execSync('./manage-webapp.sh status', {
            cwd: '/var/www/TradingAgents',
            encoding: 'utf-8'
        });
        return output;
    } catch (error) {
        return error.message;
    }
}
```

### Docker Integration (if applicable)
```bash
# Inside docker-compose.yml or similar
services:
  tradingagents:
    build: .
    ports:
      - "8011:8000"
    volumes:
      - ./logs:/app/logs
    command: python -m webapp
```

## 📞 Support Resources

- **Local Docs:** `./WEBAPP_MANAGEMENT.md`
- **Scripts Summary:** `./SCRIPTS_CREATED.md`
- **Quick Start:** `./WEBAPP_QUICK_START.sh`
- **Main README:** `./README.md`
- **Help Command:** `./manage-webapp.sh help`

## 🔗 Related Documentation

- [WEBAPP_MANAGEMENT.md](./WEBAPP_MANAGEMENT.md) - Detailed management guide
- [SCRIPTS_CREATED.md](./SCRIPTS_CREATED.md) - Script details and examples
- [README.md](./README.md) - Main project documentation
- [CHANGELOG.md](./CHANGELOG.md) - Version history

---

**Version:** 1.0  
**Last Updated:** 2026-11-13  
**Status:** ✅ Production Ready

**Created by:** Copilot Assistant  
**Environment:** Linux, Apache2, Python 3.14.4
