"""Pull-based auto-deploy: keeps the Pi up to date with master. Run on the Pi:

    python -m app.scripts.autodeploy             # what the timer runs
    python -m app.scripts.autodeploy --dry-run   # show what it would do, change nothing

GitHub cannot reach a Pi on a home network, so the Pi asks GitHub instead. Every few minutes
(keezer-deploy.timer) this fetches master. If there is something new it:

  1. refuses if the Pi's checkout has local changes or is not simply behind master,
  2. takes a database backup,
  3. runs the whole test suite on a throwaway copy of the update (nothing live is touched),
  4. fast-forwards the checkout, then installs requirements / migrates the database /
     restarts only the services whose code changed, back to back,
  5. waits for the controller to report healthy, and if it does not, rolls the code back,
  6. sends a push notification (ntfy) either way.

A failed update is reported once and is not retried until master moves on. Master is
protected on GitHub (its tests must pass before a pull request can be merged), and this
runs the tests again on the Pi, so a broken update cannot reach the freezer.

Changes to the systemd unit files (deploy/*.service, *.timer) are not installed for you; the
notification says so. See docs/operations.md.
"""
import argparse
import contextlib
import fcntl
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from collections import namedtuple

logger = logging.getLogger(__name__)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BRANCH = 'master'
STATE_PATH = os.path.expanduser('~/.keezer-deploy.json')
LOCK_PATH = os.path.expanduser('~/.keezer-deploy.lock')
STATUS_URL = 'http://127.0.0.1:5000/api/status'
HEALTH_SECONDS = 150          # how long to wait for the controller to report in after a restart
HEALTH_POLL_SECONDS = 5
FETCH_TIMEOUT = 90
TEST_TIMEOUT = 600
PIP_TIMEOUT = 900
OFFLINE_NOTIFY_AFTER = 20      # failed fetches in a row (about an hour) before it says so, once

# Services restart only when something they actually run has changed.
WEB_PATHS = ('app/views/', 'app/templates/', 'app/models/', 'app/forms/')
WEB_FILES = ('app/__init__.py', 'app/config.py')
CONTROLLER_FILES = ('app/scripts/controller.py', 'app/scripts/control_logic.py',
                    'app/scripts/fridge_control.py', 'app/scripts/thermometer.py',
                    'app/scripts/alerts.py')
CONTROLLER_PATHS = ('app/models/',)

Plan = namedtuple('Plan', 'restart_web restart_controller migrate install_requirements '
                          'units_changed kiosk_changed')


def classify(changed):
    """What an update needs, from the list of changed file paths (forward slashes)."""
    changed = [c.strip() for c in changed if c.strip()]
    migrate = any(c.startswith('migrations/versions/') and c.endswith('.py') for c in changed)
    requirements = 'requires.txt' in changed
    web = (migrate or requirements
           or any(c.startswith(WEB_PATHS) or c in WEB_FILES for c in changed))
    controller = (migrate or requirements
                  or any(c in CONTROLLER_FILES or c in WEB_FILES or c.startswith(CONTROLLER_PATHS)
                         for c in changed))
    return Plan(
        restart_web=web, restart_controller=controller, migrate=migrate,
        install_requirements=requirements,
        units_changed=any(c.startswith('deploy/') and c.endswith(('.service', '.timer')) for c in changed),
        kiosk_changed='deploy/kiosk.sh' in changed)


def services_for(plan):
    return (['keezer-web'] if plan.restart_web else []) + (['keezer'] if plan.restart_controller else [])


def real_sh(args, cwd=None, env=None, timeout=None):
    """Run a command; returns (exit code, combined output). Never raises."""
    full_env = dict(os.environ)
    full_env.update(env or {})
    try:
        done = subprocess.run(args, cwd=cwd, env=full_env, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True)
        return done.returncode, done.stdout or ''
    except subprocess.TimeoutExpired:
        return 124, 'timed out after {}s'.format(timeout)
    except OSError as exc:
        return 127, str(exc)


def status_is_healthy():
    """The web API answers and the controller has logged a reading recently."""
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=5) as response:
            return bool(json.load(response).get('controller_ok'))
    except Exception:
        return False


def _tail(text, lines=6):
    return '\n'.join((text or '').strip().splitlines()[-lines:])


class Deployer(object):
    def __init__(self, repo=REPO, sh=real_sh, notifier=None, healthy=status_is_healthy,
                 state_path=STATE_PATH, lock_path=LOCK_PATH, python=None, sleep=time.sleep):
        self.repo = repo
        self.sh = sh
        self.notifier = notifier
        self.healthy = healthy
        self.state_path = state_path
        self.lock_path = lock_path
        self.python = python or sys.executable
        self.sleep = sleep

    # --- small helpers ---

    def git(self, *args, **kw):
        return self.sh(['git'] + list(args), cwd=self.repo, timeout=kw.get('timeout', 60))

    def rev(self, ref):
        rc, out = self.git('rev-parse', ref)
        return out.strip() if rc == 0 else None

    def notify(self, title, message, priority='default', tags=''):
        logger.info('%s: %s', title, message)
        if self.notifier is not None:
            try:
                self.notifier.send(title, message, priority, tags)
            except Exception:
                logger.exception('Could not send the notification')

    def load_state(self):
        try:
            with open(self.state_path) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def save_state(self, state):
        tmp = self.state_path + '.tmp'
        with open(tmp, 'w') as handle:
            json.dump(state, handle)
        os.replace(tmp, self.state_path)

    @contextlib.contextmanager
    def lock(self):
        handle = open(self.lock_path, 'w')
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
            yield True
        finally:
            handle.close()

    # --- the run ---

    def run(self, dry_run=False):
        """One pass. Returns: up-to-date, offline, busy, skipped, dry-run, deployed, failed
        or rolled-back."""
        with self.lock() as got:
            if not got:
                logger.info('Another deploy is running; skipping')
                return 'busy'
            return self._run(dry_run)

    def _run(self, dry_run):
        rc, out = self.git('fetch', 'origin', BRANCH, timeout=FETCH_TIMEOUT)
        if rc != 0:
            logger.warning('Could not reach GitHub, will try again later: %s', _tail(out, 2))
            state = self.load_state()
            state['offline_runs'] = state.get('offline_runs', 0) + 1
            self.save_state(state)
            if state['offline_runs'] == OFFLINE_NOTIFY_AFTER:
                # a private repository or a changed address looks exactly like "no network"
                self.notify('Keezer cannot check for updates',
                            'The last {} checks could not fetch from GitHub, so the Pi is not getting updates: {}'.format(
                                OFFLINE_NOTIFY_AFTER, _tail(out, 2)), 'default', 'warning')
            return 'offline'
        state = self.load_state()
        if state.get('offline_runs'):
            state['offline_runs'] = 0
            self.save_state(state)

        head = self.rev('HEAD')
        target = self.rev('origin/' + BRANCH)
        if not head or not target:
            return self.fail(target or 'unknown', 'Could not read the git revisions.')
        if head == target:
            return 'up-to-date'
        if self.load_state().get('failed_sha') == target:
            logger.info('Update %s already failed; waiting for a newer commit', target[:7])
            return 'skipped'

        problem = self.checkout_problem()
        if problem:
            return self.fail(target, problem)

        rc, out = self.git('diff', '--name-only', head, target)
        plan = classify(out.splitlines() if rc == 0 else [])
        rc, subject = self.git('log', '-1', '--format=%s', target)
        subject = subject.strip() if rc == 0 else target[:7]
        logger.info('Update %s -> %s (%s): %s', head[:7], target[:7], subject, plan)
        if dry_run:
            print('Would deploy {} ({}):'.format(target[:7], subject))
            print('  restart: {}'.format(', '.join(services_for(plan)) or 'nothing'))
            print('  install requirements: {}   migrate: {}'.format(plan.install_requirements, plan.migrate))
            return 'dry-run'

        rc, out = self.sh([self.python, '-m', 'app.scripts.backup'], cwd=self.repo, timeout=300)
        if rc != 0:
            return self.fail(target, 'The database backup failed, so nothing was changed.\n' + _tail(out))

        ok, detail = self.test_update(target, plan)
        if not ok:
            return self.fail(target, 'The tests failed on the update, so it was not installed.\n' + detail)

        return self.apply(head, target, subject, plan)

    def checkout_problem(self):
        rc, branch = self.git('rev-parse', '--abbrev-ref', 'HEAD')
        if rc != 0 or branch.strip() != BRANCH:
            return "The Pi's checkout is on '{}', not {}.".format(branch.strip(), BRANCH)
        rc, out = self.git('status', '--porcelain', '--untracked-files=no')
        if rc != 0 or out.strip():
            return "The Pi's checkout has local changes to tracked files:\n" + _tail(out)
        rc, _ = self.git('merge-base', '--is-ancestor', 'HEAD', 'origin/' + BRANCH)
        if rc != 0:
            return "The Pi's checkout has commits that are not on {}, so it cannot be fast-forwarded.".format(BRANCH)
        return None

    def test_update(self, target, plan):
        """Run the full suite on a throwaway copy of the update; nothing live is touched."""
        work = tempfile.mkdtemp(prefix='keezer-update-')
        try:
            archive = os.path.join(work, 'update.tar')
            rc, out = self.git('archive', '--format=tar', '-o', archive, target)
            if rc != 0:
                return False, 'Could not export the update: ' + _tail(out)
            src = os.path.join(work, 'src')
            os.mkdir(src)
            with tarfile.open(archive) as tar:
                tar.extractall(src)
            if plan.install_requirements:
                # new dependencies must exist before the new code's tests can import them
                rc, out = self.sh([self.python, '-m', 'pip', 'install', '-r', os.path.join(src, 'requires.txt')],
                                  cwd=src, timeout=PIP_TIMEOUT)
                if rc != 0:
                    return False, 'Installing the new requirements failed:\n' + _tail(out)
            rc, out = self.sh([self.python, '-m', 'unittest', 'discover', '-s', 'app/tests', '-t', '.'],
                              cwd=src, env={'DATABASE_URL': 'sqlite://', 'PYTHONDONTWRITEBYTECODE': '1'},
                              timeout=TEST_TIMEOUT)
            return rc == 0, _tail(out, 10)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def apply(self, old, target, subject, plan):
        rc, out = self.git('merge', '--ff-only', 'origin/' + BRANCH)
        if rc != 0:
            return self.fail(target, 'The fast-forward failed:\n' + _tail(out))

        env = {'FLASK_APP': 'app'}
        if plan.install_requirements:
            rc, out = self.sh([self.python, '-m', 'pip', 'install', '-r', 'requires.txt'],
                              cwd=self.repo, timeout=PIP_TIMEOUT)
            if rc != 0:
                return self.roll_back(old, target, plan, 'Installing the requirements failed:\n' + _tail(out),
                                      restart=False)
        if plan.migrate:
            rc, out = self.sh([self.python, '-m', 'flask', 'db', 'upgrade'], cwd=self.repo, env=env, timeout=300)
            if rc != 0:
                return self.roll_back(old, target, plan,
                                      'The database migration failed. Check the database; a backup was taken '
                                      'just before:\n' + _tail(out), restart=False)

        services = services_for(plan)
        if services:
            rc, out = self.sh(['sudo', '-n', 'systemctl', 'restart'] + services, cwd=self.repo, timeout=120)
            if rc != 0:
                return self.roll_back(old, target, plan, 'Restarting {} failed:\n{}'.format(
                    ', '.join(services), _tail(out)), restart=True)
            if not self.wait_healthy():
                return self.roll_back(old, target, plan,
                                      'The controller did not report healthy within {}s of the restart.'.format(
                                          HEALTH_SECONDS), restart=True)

        self.save_state({})
        notes = []
        if plan.units_changed:
            notes.append('The systemd unit files changed: copy them to /etc/systemd/system and run '
                         'daemon-reload (docs/operations.md).')
        if plan.kiosk_changed:
            notes.append('The kiosk script changed: it takes effect the next time the kiosk starts.')
        self.notify('Keezer updated', '{} ({}). Restarted: {}.{}'.format(
            target[:7], subject, ', '.join(services) or 'nothing (no restart needed)',
            ''.join('\n' + n for n in notes)), 'default', 'white_check_mark')
        return 'deployed'

    def wait_healthy(self):
        waited = 0
        while waited <= HEALTH_SECONDS:
            if self.healthy():
                return True
            self.sleep(HEALTH_POLL_SECONDS)
            waited += HEALTH_POLL_SECONDS
        return False

    def roll_back(self, old, target, plan, reason, restart):
        """Put the previous code back. The database is not touched: migrations only add things."""
        rc, out = self.git('reset', '--hard', old)
        message = reason + '\n'
        if rc != 0:
            message += 'ALSO FAILED to put the old code back: ' + _tail(out)
        elif restart and services_for(plan):
            rc, out = self.sh(['sudo', '-n', 'systemctl', 'restart'] + services_for(plan),
                              cwd=self.repo, timeout=120)
            ok = rc == 0 and self.wait_healthy()
            message += ('Rolled back to {} and the controller is healthy again.' if ok else
                        'Rolled back to {} but the controller is NOT healthy: check it now.').format(old[:7])
        else:
            message += 'Rolled back to {}.'.format(old[:7])
        self.save_state({'failed_sha': target})
        self.notify('Keezer update rolled back', '{}: {}'.format(target[:7], message), 'high', 'warning')
        return 'rolled-back'

    def fail(self, target, reason):
        self.save_state({'failed_sha': target})
        self.notify('Keezer update failed', '{}: {}'.format(target[:7], reason), 'high', 'warning')
        return 'failed'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Update the Pi to the latest master, safely.')
    parser.add_argument('--dry-run', action='store_true', help='show what would happen and change nothing')
    parser.add_argument('--repo', default=REPO, help='the checkout to update (default: this one)')
    parser.add_argument('--state', default=STATE_PATH, help='where it remembers a failed update')
    parser.add_argument('--lock', default=LOCK_PATH, help='lock file that stops two runs overlapping')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    from app.scripts.alerts import Notifier
    result = Deployer(repo=os.path.abspath(args.repo), notifier=Notifier(), state_path=args.state,
                      lock_path=args.lock).run(dry_run=args.dry_run)
    logger.info('Result: %s', result)
    return 1 if result in ('failed', 'rolled-back') else 0


if __name__ == '__main__':
    sys.exit(main())
