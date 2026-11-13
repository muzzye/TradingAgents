# 📜 Script di Gestione WebApp - File Creati

## 📁 File Creati

### 1. **manage-webapp.sh** (Consigliato) ⭐
- **Tipo:** Script bash
- **Perché:** Perfetto per automazioni, cron jobs, CI/CD
- **Uso:** `./manage-webapp.sh [start|stop|restart|status]`
- **Caratteristiche:**
  - Gestione Apache2 automatica
  - Controlli di salute dell'API
  - Output colorato e leggibile
  - Logging automatico
  - Timeout intelligenti

### 2. **manage_webapp.py** (Alternativa)
- **Tipo:** Script Python
- **Perché:** Integrazione con script Python/automazioni
- **Uso:** `python manage_webapp.py [start|stop|restart|status]`
- **Caratteristiche:**
  - Stesso funzionamento del bash
  - Facile integrazione con Python
  - JSON parsing per API response
  - Cross-platform friendly

### 3. **WEBAPP_MANAGEMENT.md** (Documentazione)
- **Tipo:** Markdown documentation
- **Contenuto:**
  - Architettura del sistema
  - Guide di troubleshooting
  - Comandi di monitoraggio
  - Configurazione

### 4. **WEBAPP_QUICK_START.sh** (Quick Reference)
- **Tipo:** Bash (info only, non eseguibile)
- **Contenuto:** Cheat sheet di comandi

## 🎯 Come Usare

### Caso 1: Controllo Rapido
```bash
cd /var/www/TradingAgents
./manage-webapp.sh status
```

### Caso 2: Riavvio
```bash
./manage-webapp.sh restart
```

### Caso 3: Automazione Cron
```bash
# Riavvia la webapp ogni mattina alle 2 AM
0 2 * * * /var/www/TradingAgents/manage-webapp.sh restart >> /var/www/TradingAgents/logs/cron.log 2>&1
```

### Caso 4: Monitoraggio Continuo
```bash
watch -n 5 '/var/www/TradingAgents/manage-webapp.sh status'
```

### Caso 5: Integrazione Python
```python
import subprocess
result = subprocess.run(
    ["./manage-webapp.sh", "status"],
    cwd="/var/www/TradingAgents",
    capture_output=True,
    text=True
)
print(result.stdout)
```

## 🔍 Verifica che Tutto Funziona

```bash
# 1. Controlla lo stato
./manage-webapp.sh status

# 2. Verifica la web console
curl http://localhost:8011 | head -20

# 3. Verifica l'API
curl http://localhost:8011/api/health | jq .

# 4. Leggi i log
tail -20 /var/www/TradingAgents/logs/webapp.log
```

## 🚨 Troubleshooting Rapido

| Problema | Soluzione |
|----------|-----------|
| "API not responding" | `./manage-webapp.sh restart` |
| "Address already in use" | `lsof -i :8011` e `kill -9 <PID>` |
| "Permission denied" | `chmod +x manage-webapp.sh` |
| "Ctrl-C non ferma il processo" | `kill -9 <PID>` |

## 📊 Struttura del Progetto

```
/var/www/TradingAgents/
├── manage-webapp.sh              ← Script principale (bash)
├── manage_webapp.py              ← Script alternativo (python)
├── WEBAPP_MANAGEMENT.md          ← Documentazione completa
├── WEBAPP_QUICK_START.sh         ← Cheat sheet
├── SCRIPTS_CREATED.md            ← Questo file
├── logs/                         ← Directory log
│   └── webapp.log                ← Log della webapp
├── .venv/                        ← Virtual environment
├── webapp/                       ← Codice webapp
└── README.md                     ← Documentazione principale
```

## 🔐 Note di Sicurezza

- I log non contengono credenziali (per sicurezza)
- La webapp ascolta solo su localhost:8000
- Apache proxya su localhost:8011
- Nessun esposizione diretta a internet per impostazione predefinita
- Gli script richiedono `sudo` solo per Apache (necessario)

## 🆘 Per Aiuto

```bash
./manage-webapp.sh help
# oppure
cat WEBAPP_MANAGEMENT.md
```

---

**Data:** 2026-11-13
**Version:** 1.0
**Status:** ✅ Produzione

