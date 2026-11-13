# WebApp Management Scripts

Questa cartella contiene gli script per gestire il processo Python della webapp TradingAgents.

## 🚀 Quick Start

```bash
# Status
./manage-webapp.sh status

# Start
./manage-webapp.sh start

# Stop
./manage-webapp.sh stop

# Restart
./manage-webapp.sh restart

# Help
./manage-webapp.sh help
```

## 📋 Script: `manage-webapp.sh`

Script bash completo per gestire il servizio webapp di TradingAgents via Apache2.

**Utilizzo:**
```bash
./manage-webapp.sh [start|stop|restart|status|help]
```

**Comandi:**
- `start` - Avvia Apache2 e la webapp
- `stop` - Ferma Apache2 e la webapp
- `restart` - Riavvia Apache2 e la webapp
- `status` - Mostra lo stato corrente
- `help` - Mostra l'aiuto

**Output di esempio:**
```
[INFO] Checking TradingAgents webapp status...
[SUCCESS] ✅ Apache2 is running
[SUCCESS] ✅ API is responding
   Status: ok
   Version: 0.8.7
   URL: http://localhost:8011
[INFO] View logs with: tail -f /var/www/TradingAgents/logs/webapp.log
```

## 🏗️ Architettura

```
┌─────────────────────────────────────────┐
│   User Browser                          │
│   http://localhost:8011                 │
└────────────────┬────────────────────────┘
                 │
                 ▼
         ┌───────────────┐
         │   Apache2     │
         │   (Proxy)     │
         │   Port 8011   │
         └───────┬───────┘
                 │
                 ▼
         ┌───────────────────┐
         │  Python Process   │
         │  (FastAPI/Uvicorn)│
         │  Port 8000        │
         └───────────────────┘
```

## 📊 Monitoraggio

**Visualizza lo stato del processo:**
```bash
ps aux | grep "python -m webapp"
```

**Visualizza i log della webapp:**
```bash
tail -f /var/www/TradingAgents/logs/webapp.log
```

**Visualizza i log di Apache:**
```bash
tail -f /var/log/apache2/error.log
tail -f /var/log/apache2/access.log
```

**Controlla la porta 8011:**
```bash
curl http://localhost:8011/api/health | jq .
```

## 🔧 Configurazione

Gli script supportano automaticamente:
- Rilevamento della porta corretta (8011 per proxy, 8000 per processo diretto)
- Controllo dell'ambiente virtuale Python
- Logging automatico
- Timeout intelligenti (fino a 30 secondi per l'API)

## ⚠️ Troubleshooting

**La API non risponde:**
```bash
# Controlla Apache
sudo systemctl status apache2

# Controlla i log di Apache
sudo tail -50 /var/log/apache2/error.log

# Controlla i log della webapp
tail -50 /var/www/TradingAgents/logs/webapp.log
```

**Porta già in uso:**
```bash
# Controlla quale processo usa la porta 8011
lsof -i :8011

# Controlla quale processo usa la porta 8000
lsof -i :8000

# Forza il riavvio completo
./manage-webapp.sh restart
```

**Problema di permessi:**
```bash
# Gli script possono richiedere sudo per Apache
# Se viene richiesta password, assicurati di avere i permessi

# Oppure esegui direttamente:
sudo systemctl restart apache2
```

## 📝 Note

- Lo script usa `sudo` automaticamente per le operazioni su Apache2
- I log vengono salvati in `/var/www/TradingAgents/logs/webapp.log`
- Il PID file temporaneo è `/tmp/tradingagents-webapp.pid`
- La webapp è accessibile solo localmente (127.0.0.1) per sicurezza

## 🔗 Link Utili

- Web Console: http://localhost:8011
- API Health: http://localhost:8011/api/health
- API Docs: http://localhost:8011/docs (se disponibile)

---

**Ultimo aggiornamento:** 2026-11-13
