#!/usr/bin/env bash
# Hearth setup for Raspberry Pi 5 (also fine on a Pi 4). Safe to re-run. No full-upgrade.
#   scripts/pi_setup.sh        (as your normal user; it calls sudo itself)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
MODEL=vosk-model-small-en-us-0.15
cd "$REPO"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user, not with sudo (it uses sudo where needed)."
  exit 1
fi

echo "== 1/5 apt packages"
sudo apt-get update
# gpiozero + lgpio come from apt (pip-building lgpio fails on the Pi). PortAudio is needed by sounddevice.
sudo apt-get install -y python3-venv python3-gpiozero python3-lgpio libportaudio2 avahi-daemon

echo "== 2/5 Python venv (.venv, sees apt's gpiozero/lgpio)"
if ! grep -qs "include-system-site-packages = true" .venv/pyvenv.cfg; then
  rm -rf .venv
  python3 -m venv --system-site-packages .venv
fi
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -c "import gpiozero, lgpio, vosk, sounddevice, flask, dotenv; print('imports OK')"

echo "== 3/5 Vosk model"
if [ -d "$MODEL" ] || [ -d "models/$MODEL" ]; then
  echo "model present"
else
  curl -fL -o "/tmp/$MODEL.zip" "https://alphacephei.com/vosk/models/$MODEL.zip"
  python3 -m zipfile -e "/tmp/$MODEL.zip" .
  rm -f "/tmp/$MODEL.zip"
  echo "model downloaded"
fi

echo "== 4/5 groups"
for g in gpio audio; do
  if id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx "$g"; then
    echo "$USER_NAME is in $g"
  else
    sudo usermod -aG "$g" "$USER_NAME"
    echo "ADDED $USER_NAME to $g -- reboot before running Hearth so it takes effect"
  fi
done

echo "== 5/5 systemd service"
# Renamed from sixsense, then brasle: remove old services so two copies never fight over the rings and mic.
for old in sixsense brasle; do
  if [ -f /etc/systemd/system/$old.service ]; then
    sudo systemctl disable --now $old || true
    sudo rm -f /etc/systemd/system/$old.service
    echo "removed old $old service"
  fi
done
sed -e "s|<user>|$USER_NAME|g" -e "s|<repo>|$REPO|g" scripts/hearth.service \
  | sudo tee /etc/systemd/system/hearth.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable hearth
echo "installed and enabled: starts on every boot (not started now)"

cat <<DONE

Setup done. Next:
  .venv/bin/python main.py --doctor --device USB     # every line should say PASS
  sudo systemctl start hearth                      # or reboot; the glove buzzes "ready" (1-2-3 sweep)
  journalctl -u hearth -f                          # logs
  iPhone: http://$(hostname).local:8080/tap   Laptop: http://$(hostname).local:8080/
DONE
