#!/bin/bash
# Wall display launcher: waits for the web service, then opens the dashboard in a
# full-screen Chromium. Started by the LXDE session autostart (see README, "Wall
# display"). Writes what it does to ~/kiosk.log so a failed start can be diagnosed.
exec >>"$HOME/kiosk.log" 2>&1
echo "$(date +%T) kiosk: session started, waiting for the web service"
until curl -sf http://127.0.0.1:5000/api/status >/dev/null; do sleep 2; done
echo "$(date +%T) kiosk: web service answered, starting chromium"
exec chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
  --check-for-update-interval=31536000 "http://127.0.0.1:5000/?display=1"
