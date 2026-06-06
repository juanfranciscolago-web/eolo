#!/bin/bash
# ============================================================
# INSTALL RETEST SCHEDULE — launchd job en la Mac de Juan
#
# Instala UN job que corre el monitor del cohort RETEST_V1 cada
# día de semana a las 11:00 ET. El monitor:
#   - escribe docs/retest_status/latest.json (lo lee la tarea de Claude)
#   - cuando llega review_at (03-jul) dispara la evaluación final CI95
#
# Esto es el "backbone" autónomo: corre con tus credenciales gcloud/Firestore
# y NO depende de que la app de Claude esté abierta.
#
# Uso:
#   bash tools/install_retest_schedule.sh          # instala
#   bash tools/install_retest_schedule.sh --remove # desinstala
# ============================================================
set -e

LABEL="com.eolo.retest-monitor"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
REPO="$HOME/PycharmProjects/eolo"
PYTHON="$(command -v python3)"
LOG="$REPO/docs/retest_status/launchd.log"

if [[ "$1" == "--remove" ]]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "✅ Desinstalado $LABEL"
    exit 0
fi

mkdir -p "$REPO/docs/retest_status"

# 11:00 hora LOCAL de la Mac. Si tu Mac está en ET, esto es 11:00 ET.
# Ajustá Hour si tu timezone no es ET (ej. ART = ET+1 o +2 según DST).
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${REPO}/tools/v1_retest_monitor.py</string>
    </array>
    <key>WorkingDirectory</key><string>${REPO}</string>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
    </array>
    <key>StandardOutPath</key><string>${LOG}</string>
    <key>StandardErrorPath</key><string>${LOG}</string>
    <key>RunAtLoad</key><false/>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "✅ Instalado $LABEL — corre lun-vie 11:00 local"
echo "   plist: $PLIST"
echo "   log:   $LOG"
echo ""
echo "Probar ahora mismo (sin esperar al schedule):"
echo "   python3 $REPO/tools/v1_retest_monitor.py"
