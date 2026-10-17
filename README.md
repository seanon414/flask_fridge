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

Tests: `python -m unittest discover -s app/tests -t .`

## Services (Raspberry Pi)

Unit files live in `deploy/`:

```
sudo cp deploy/keezer.service deploy/keezer-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now keezer keezer-web
```

`keezer` is the control loop; `keezer-web` serves the UI on port 5000 with waitress.
Put `SECRET_KEY=<random string>` in `.env` (gitignored) so sessions survive restarts.
