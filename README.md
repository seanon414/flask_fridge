# Lager keezer

Tempature control a chest freezer with raspberry pi, GPIO code signals and thermostat.

## Running Flask

### Clone this repo

1. Export flask app env var
```
export FLASK_APP=app
```

2. Initialize database
```
flask db init
```

3. Run Flask app
```
flask run
```


## Setup

### Wiringpi

1. To obtain WiringPi using GIT:
```
git clone git://git.drogon.net/wiringPi
```

2. If you have already used the clone operation for the first time, then
```
cd wiringPi
git pull origin
```

3. Will fetch an updated version then you can re-run the build script below. To build/install there is a new simplified script:
```
cd wiringPi
./build
```

4. Test wiringPi installation. Run the gpio command to check the installation:
```
gpio -v
gpio readall
```

### RPiutils
1. From your RPi, clone this archive:
```
git clone --recursive git://github.com/ninjablocks/433Utils.git (recursive ensure that the rc-switch submodule gets populated which is needed by RPi_utils)
```

2. Go to utils
```
cd 433Utils/RPi_utils
```

3. Sniff remote code
```
./RFSniffer
```

4. User codesend to simulate buttons on remote
```
./codesend *REMOTE CODE*
```

### Temp and code censor

1. To add support, we first need to open up the boot config file, and this can be done by running the following command:
```
sudo vim /boot/config.txt
```

2. At the bottom of this file enter the following.
```
dtoverlay=w1-gpio
```

3. Once done save & exit. Now reboot the Pi.
```
sudo reboot
```

## Controller

The thermostat logic lives in one long-running process that replaces the old
cron + `lager.py` setup. The web app only edits settings in the database; the
controller is the only thing that talks to the RF transmitter.

```
export FLASK_APP=app
flask db upgrade                    # adds hysteresis/mode columns and the reading log
python -m app.scripts.controller    # run as a systemd service on the Pi
```

- Turns on above `target + hysteresis`, off below `target - hysteresis`.
- Won't switch state again within `min_cycle_seconds` (default 300) of the last change.
- If the sensor can't be read it holds the current state and logs a warning.
- Mode `auto` follows the thermostat; `on`/`off` force the fridge (set from the web UI).
- Every cycle (30s) is logged to the `reading` table; rows older than 90 days are pruned.
- Overridable via env: `CODESEND_PATH`, `FRIDGE_CODE_ON`, `FRIDGE_CODE_OFF`, `SECRET_KEY`, `DATABASE_URL`.

### Coast-aware control (optional)

A chest freezer keeps cooling for a while after the compressor stops, so a plain
thermostat overshoots its low edge (on the author's keezer: a 1°F band swung about
2.8°F, averaging well under the target). With a coast time set, the controller watches
how fast the temperature is falling and stops early, once
`temp - cooling rate × coast minutes` reaches the low edge. It never stops more than
2°F early, never breaks the minimum cycle time, and does nothing until it has about
three minutes of readings (e.g. just after a restart). Off by default (`0`).

```
export FLASK_APP=app
flask db upgrade                        # adds the coast_minutes setting (once)
python -m app.scripts.coast             # study the last 3 days and suggest a value
python -m app.scripts.coast set 18      # turn it on; takes effect within 30 s, no restart
python -m app.scripts.coast set 0       # back to a plain thermostat
```

The study measures every complete compressor stop (readings unbroken from a few minutes
before the stop until the next start), divides how far the temperature kept falling by
how fast it was falling, and suggests the median. Re-run it after a day or two with
coast on and adjust. The controller also logs each stop's real coast to the journal
(`journalctl -u keezer | grep Coast:`). Trade-off: stopping earlier means shorter
compressor runs and more cycles per day. If you would rather have fewer starts, widen
the swing instead. Changes made with `set` show up as markers on the history chart.

Tests (API tests need an in-memory DB so they never touch app.db):

```
DATABASE_URL=sqlite:// python -m unittest discover -s app/tests -t .
```

## Services (Raspberry Pi)

Unit files live in `deploy/`:

```
sudo cp deploy/keezer.service deploy/keezer-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now keezer keezer-web
```

`keezer` is the control loop; `keezer-web` serves the UI on port 5000 with waitress.
Put `SECRET_KEY=<random string>` in `.env` (gitignored) so sessions survive restarts.

### Frontend tests (dev machine only)

The browser script is tested under Node with jsdom; the Pi never needs this.

```
npm install
npm test
```

## Alerts

Push notifications go through [ntfy](https://ntfy.sh) and are off until you set a topic.

1. Install the ntfy app on your phone and subscribe to a long random topic name.
2. Put it in `.env` (see `.env.example`): `NTFY_TOPIC=your-long-random-topic`
3. Install the new units and restart (the controller now reads `.env` too):
   ```
   sudo cp deploy/*.service deploy/*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now keezer-watchdog.timer
   sudo systemctl restart keezer keezer-web
   ```
4. Check it works: `set -a; . ./.env; set +a; python -m app.scripts.alerts test`

You are alerted when the freezer is too warm or too cold for a sustained stretch
(3 degF outside the band for 30 min by default), when the sensor has failed for
10 min, or when the controller has stopped logging (the watchdog, every 2 min).
Each alert is sent once, repeated after 6 h while it lasts, and followed by a
"resolved" notice. Active alerts also show in the web UI.

## Setting changes on the chart

Target, swing and mode changes made in the UI are logged (`event` table) and drawn
as dashed markers on the history chart; hover one to see what changed.

## Wall display (a monitor on the Pi)

Open `http://127.0.0.1:5000/?display=1` for a big view that fills the screen: large
temperature, a tall history chart, and the controls in a panel beside it (for a mouse
and keyboard on the Pi). Add `&readonly=1` to hide the controls. The page reloads
itself when the server is updated, returns to the 24h chart after 5 idle minutes, and
hides the mouse cursor when idle.

To start it automatically in a full-screen kiosk browser on Raspberry Pi OS
"Bullseye" (check with `cat /etc/os-release`; the desktop uses LXDE and X11):

Run this from the project folder on the Pi:

```
mkdir -p ~/.config/lxsession/LXDE-pi
cp /etc/xdg/lxsession/LXDE-pi/autostart ~/.config/lxsession/LXDE-pi/autostart
sed -i '/xscreensaver/d' ~/.config/lxsession/LXDE-pi/autostart
cat >> ~/.config/lxsession/LXDE-pi/autostart <<EOT
@xset s off
@xset -dpms
@xset s noblank
@$PWD/deploy/kiosk.sh
EOT
sudo reboot
```

The `sed` line removes the default `xscreensaver`: left running, it blanks the screen
with a random pattern after about 10 minutes without a keypress (it looks like a white
or multicoloured screen, with the dashboard still running underneath), and `xset s off`
does not stop it. The `xset` lines stop the screen going blank. [`deploy/kiosk.sh`](deploy/kiosk.sh) waits
for the web service to come up, so the browser never starts on an error page, and then
opens the dashboard full screen. It logs to `~/kiosk.log`; if the screen stays on the
desktop, read that file first. It uses `127.0.0.1` rather than `localhost` on purpose:
the web server only listens on IPv4, so a damaged `/etc/hosts` that resolves `localhost`
to IPv6 only would otherwise stop the kiosk starting.

To leave kiosk mode, press `Alt+F4`, or from another machine run
`ssh pi@<ip> pkill chromium`. On the newer "Bookworm" OS (Wayland) the autostart file is
different; ask and we'll adapt it.

## Data and backups

The database lives **outside the git repo**, at `~/keezer-data/app.db` (override with
`KEEZER_DATA_DIR` or `DATABASE_URL`). Git can silently overwrite or delete files inside
the repo when you switch branches or pull, so never keep live data there.

Existing installs that still have `app/app.db` keep working until you move it:

```
cd ~/Documents/code/flask_fridge
cp app/app.db ~/app.db.before-move            # a safety copy first
sudo systemctl stop keezer keezer-web keezer-watchdog.timer
mkdir -p ~/keezer-data && cp app/app.db ~/keezer-data/app.db
python -c "import sqlite3; print(sqlite3.connect('/home/pi/keezer-data/app.db').execute('select count(*) from reading').fetchone())"
mv app/app.db app/app.db.moved
sudo systemctl start keezer keezer-web keezer-watchdog.timer
```

A nightly backup (03:30, kept for 14 days, in `~/keezer-backups`) is installed with:

```
sudo cp deploy/keezer-backup.service deploy/keezer-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now keezer-backup.timer
python -m app.scripts.backup                  # run one now to check it works
```

Restore from a backup:

```
sudo systemctl stop keezer keezer-web keezer-watchdog.timer
cp ~/keezer-backups/app.db.<timestamp> ~/keezer-data/app.db
sudo systemctl start keezer keezer-web keezer-watchdog.timer
```

Take a backup (`python -m app.scripts.backup`) before every `flask db upgrade`.
