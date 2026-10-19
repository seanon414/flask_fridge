"""Auto-deploy tests. Real git repositories in temporary folders; every other command
(tests, backup, pip, migrations, systemctl) is faked, so nothing on the machine is touched."""
import fcntl
import os
import subprocess
import tempfile
import unittest

from app.scripts.autodeploy import Deployer, classify, real_sh, services_for


def git(cwd, *args):
    return subprocess.check_output(
        ['git', '-c', 'user.name=Test', '-c', 'user.email=t@example.com'] + list(args),
        cwd=cwd, universal_newlines=True, stderr=subprocess.STDOUT).strip()


class ClassifyTests(unittest.TestCase):
    def plan(self, *files):
        return classify(list(files))

    def test_docs_tests_and_workflows_need_nothing(self):
        for f in ('README.md', 'docs/operations.md', 'app/tests/test_api.py', 'frontend_tests/ui.test.js',
                  '.github/workflows/ci.yml', 'package.json'):
            plan = self.plan(f)
            self.assertEqual(services_for(plan), [], f)
            self.assertFalse(plan.migrate or plan.install_requirements, f)

    def test_static_files_alone_need_no_restart(self):
        self.assertEqual(services_for(self.plan('app/static/app.js', 'app/static/app.css')), [])

    def test_a_template_or_view_restarts_the_web_service_only(self):
        self.assertEqual(services_for(self.plan('app/templates/index.html')), ['keezer-web'])
        self.assertEqual(services_for(self.plan('app/views/api.py')), ['keezer-web'])
        self.assertEqual(services_for(self.plan('app/static/app.js', 'app/templates/index.html')), ['keezer-web'])

    def test_controller_code_restarts_the_controller_only(self):
        for f in ('app/scripts/controller.py', 'app/scripts/control_logic.py', 'app/scripts/fridge_control.py',
                  'app/scripts/thermometer.py', 'app/scripts/alerts.py'):
            self.assertEqual(services_for(self.plan(f)), ['keezer'], f)

    def test_one_shot_scripts_restart_nothing(self):
        for f in ('app/scripts/backup.py', 'app/scripts/watchdog.py', 'app/scripts/coast.py',
                  'app/scripts/autodeploy.py'):
            self.assertEqual(services_for(self.plan(f)), [], f)

    def test_models_and_config_restart_both(self):
        for f in ('app/models/temp.py', 'app/__init__.py', 'app/config.py'):
            self.assertEqual(services_for(self.plan(f)), ['keezer-web', 'keezer'], f)

    def test_a_migration_migrates_and_restarts_both(self):
        plan = self.plan('migrations/versions/abc_add_column.py')
        self.assertTrue(plan.migrate)
        self.assertEqual(services_for(plan), ['keezer-web', 'keezer'])

    def test_requirements_install_and_restart_both(self):
        plan = self.plan('requires.txt')
        self.assertTrue(plan.install_requirements)
        self.assertEqual(services_for(plan), ['keezer-web', 'keezer'])

    def test_unit_files_and_the_kiosk_script_are_flagged_not_applied(self):
        plan = self.plan('deploy/keezer.service', 'deploy/keezer-deploy.timer')
        self.assertTrue(plan.units_changed)
        self.assertEqual(services_for(plan), [])
        self.assertTrue(self.plan('deploy/kiosk.sh').kiosk_changed)

    def test_blank_lines_are_ignored(self):
        self.assertEqual(services_for(classify(['', '  ', 'README.md'])), [])


class FakeSh(object):
    """Real git (the repositories are real); everything else is recorded and answered from `results`."""
    def __init__(self):
        self.calls = []
        self.results = {}      # marker -> (rc, output)
        self.sequences = {}    # marker -> [answers used one per call, then `results`]

    @staticmethod
    def marker(args):
        if 'app.scripts.backup' in args:
            return 'backup'
        if 'unittest' in args:
            return 'tests'
        if 'pip' in args:
            return 'pip'
        if 'flask' in args and 'upgrade' in args:
            return 'migrate'
        if 'systemctl' in args:
            return 'restart'
        return 'other'

    def __call__(self, args, cwd=None, env=None, timeout=None):
        marker = 'git' if args[0] == 'git' else self.marker(args)
        self.calls.append((marker, list(args), cwd, env))
        if marker == 'git':
            return real_sh(args, cwd=cwd, env=env, timeout=timeout)
        if self.sequences.get(marker):
            return self.sequences[marker].pop(0)
        return self.results.get(marker, (0, 'ok'))

    def markers(self):
        return [c[0] for c in self.calls if c[0] != 'git']


class FakeNotifier(object):
    def __init__(self):
        self.sent = []

    def send(self, title, message, priority='default', tags=''):
        self.sent.append((title, message, priority))
        return True


class DeployerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.origin = os.path.join(self.dir, 'origin.git')
        self.seed = os.path.join(self.dir, 'seed')
        self.pi = os.path.join(self.dir, 'pi')

        git(self.dir, 'init', '--bare', self.origin)
        git(self.origin, 'symbolic-ref', 'HEAD', 'refs/heads/master')
        os.makedirs(self.seed)
        git(self.seed, 'init')
        git(self.seed, 'checkout', '-b', 'master')
        git(self.seed, 'remote', 'add', 'origin', self.origin)
        self.write(self.seed, {'README.md': 'v1', 'app/scripts/controller.py': 'v1', 'requires.txt': 'flask'})
        git(self.seed, 'add', '-A')
        git(self.seed, 'commit', '-m', 'first')
        git(self.seed, 'push', '-u', 'origin', 'master')
        git(self.dir, 'clone', self.origin, self.pi)

        self.sh = FakeSh()
        self.notifier = FakeNotifier()
        self.health = []                      # answers for successive health checks; default healthy
        self.deployer = Deployer(
            repo=self.pi, sh=self.sh, notifier=self.notifier, healthy=self.is_healthy,
            state_path=os.path.join(self.dir, 'state.json'), lock_path=os.path.join(self.dir, 'lock'),
            python='python-under-test', sleep=lambda s: None)

    def is_healthy(self):
        return self.health.pop(0) if self.health else True

    @staticmethod
    def write(root, files):
        for name, text in files.items():
            path = os.path.join(root, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as handle:
                handle.write(text)

    def publish(self, files, message='update'):
        self.write(self.seed, files)
        git(self.seed, 'add', '-A')
        git(self.seed, 'commit', '-m', message)
        git(self.seed, 'push', 'origin', 'master')
        return git(self.seed, 'rev-parse', 'HEAD')

    def head(self):
        return git(self.pi, 'rev-parse', 'HEAD')

    def restarts(self):
        return [c[1][4:] for c in self.sh.calls if c[0] == 'restart']      # drop: sudo -n systemctl restart

    # --- nothing to do ---

    def test_up_to_date_does_nothing(self):
        self.assertEqual(self.deployer.run(), 'up-to-date')
        self.assertEqual(self.sh.markers(), [])
        self.assertEqual(self.notifier.sent, [])

    def test_no_connection_is_quiet_and_retried_later(self):
        git(self.pi, 'remote', 'set-url', 'origin', os.path.join(self.dir, 'nowhere.git'))
        self.assertEqual(self.deployer.run(), 'offline')
        self.assertEqual(self.sh.markers(), [])
        self.assertEqual(self.notifier.sent, [])

    def test_a_long_run_of_failed_fetches_is_reported_once(self):
        from app.scripts.autodeploy import OFFLINE_NOTIFY_AFTER
        good_url = git(self.pi, 'remote', 'get-url', 'origin')
        git(self.pi, 'remote', 'set-url', 'origin', os.path.join(self.dir, 'nowhere.git'))
        for _ in range(OFFLINE_NOTIFY_AFTER - 1):
            self.assertEqual(self.deployer.run(), 'offline')
        self.assertEqual(self.notifier.sent, [])                      # not yet
        self.deployer.run()
        self.assertEqual([t for t, _, _ in self.notifier.sent], ['Keezer cannot check for updates'])
        for _ in range(5):
            self.deployer.run()
        self.assertEqual(len(self.notifier.sent), 1)                  # and not again

        git(self.pi, 'remote', 'set-url', 'origin', good_url)          # the connection comes back
        self.assertEqual(self.deployer.run(), 'up-to-date')
        git(self.pi, 'remote', 'set-url', 'origin', os.path.join(self.dir, 'nowhere.git'))
        for _ in range(OFFLINE_NOTIFY_AFTER):
            self.deployer.run()
        self.assertEqual(len(self.notifier.sent), 2)                  # the count started again from zero

    # --- a good update ---

    def test_a_docs_only_update_is_tested_and_installed_but_restarts_nothing(self):
        new = self.publish({'README.md': 'v2'}, 'Docs: tweak the readme')
        self.assertEqual(self.deployer.run(), 'deployed')
        self.assertEqual(self.head(), new)
        self.assertEqual(self.sh.markers(), ['backup', 'tests'])
        self.assertEqual(self.restarts(), [])
        [(title, message, _)] = self.notifier.sent
        self.assertEqual(title, 'Keezer updated')
        self.assertIn('Docs: tweak the readme', message)
        self.assertIn('no restart needed', message)

    def test_the_tests_run_on_a_copy_with_an_in_memory_database(self):
        self.publish({'README.md': 'v2'})
        self.deployer.run()
        marker, args, cwd, env = next(c for c in self.sh.calls if c[0] == 'tests')
        self.assertEqual(env['DATABASE_URL'], 'sqlite://')
        self.assertNotEqual(os.path.realpath(cwd), os.path.realpath(self.pi))   # not the live checkout
        self.assertFalse(os.path.exists(cwd), 'the throwaway copy is removed afterwards')

    def test_a_controller_change_restarts_only_the_controller_and_waits_for_health(self):
        self.publish({'app/scripts/controller.py': 'v2'})
        self.health = [False, False, True]
        self.assertEqual(self.deployer.run(), 'deployed')
        self.assertEqual(self.restarts(), [['keezer']])
        self.assertEqual(self.health, [])                       # all three checks were used
        self.assertIn('Restarted: keezer.', self.notifier.sent[0][1])

    def test_a_migration_runs_in_order_before_the_restart(self):
        self.publish({'migrations/versions/abc_x.py': 'm', 'app/models/temp.py': 'm'})
        self.assertEqual(self.deployer.run(), 'deployed')
        self.assertEqual(self.sh.markers(), ['backup', 'tests', 'migrate', 'restart'])
        self.assertEqual(self.restarts(), [['keezer-web', 'keezer']])      # back to back, one command
        migrate = next(c for c in self.sh.calls if c[0] == 'migrate')
        self.assertEqual(migrate[3], {'FLASK_APP': 'app'})

    def test_new_requirements_are_installed_before_the_tests_and_again_for_the_live_copy(self):
        self.publish({'requires.txt': 'flask\nnewthing'})
        self.assertEqual(self.deployer.run(), 'deployed')
        self.assertEqual(self.sh.markers(), ['backup', 'pip', 'tests', 'pip', 'restart'])

    def test_unit_file_changes_are_reported_not_applied(self):
        self.publish({'deploy/keezer.service': 'x'})
        self.assertEqual(self.deployer.run(), 'deployed')
        self.assertEqual(self.restarts(), [])
        self.assertIn('systemd unit files changed', self.notifier.sent[0][1])

    def test_several_commits_at_once_deploy_the_latest(self):
        self.publish({'README.md': 'v2'}, 'one')
        last = self.publish({'README.md': 'v3'}, 'two')
        self.assertEqual(self.deployer.run(), 'deployed')
        self.assertEqual(self.head(), last)

    # --- a bad update ---

    def test_failing_tests_stop_everything(self):
        old = self.head()
        self.publish({'app/scripts/controller.py': 'broken'})
        self.sh.results['tests'] = (1, 'FAILED (failures=2)')
        self.assertEqual(self.deployer.run(), 'failed')
        self.assertEqual(self.head(), old)                       # not pulled
        self.assertEqual(self.restarts(), [])
        [(title, message, priority)] = self.notifier.sent
        self.assertEqual(title, 'Keezer update failed')
        self.assertIn('FAILED (failures=2)', message)
        self.assertEqual(priority, 'high')

    def test_a_failed_update_is_reported_once_then_retried_only_for_a_newer_commit(self):
        self.publish({'README.md': 'bad'})
        self.sh.results['tests'] = (1, 'FAILED')
        self.assertEqual(self.deployer.run(), 'failed')
        self.assertEqual(self.deployer.run(), 'skipped')
        self.assertEqual(self.deployer.run(), 'skipped')
        self.assertEqual(len(self.notifier.sent), 1)

        fixed = self.publish({'README.md': 'fixed'})
        self.sh.results['tests'] = (0, 'OK')
        self.assertEqual(self.deployer.run(), 'deployed')
        self.assertEqual(self.head(), fixed)

    def test_a_backup_failure_stops_before_any_change(self):
        old = self.head()
        self.publish({'README.md': 'v2'})
        self.sh.results['backup'] = (1, 'Database is missing or empty')
        self.assertEqual(self.deployer.run(), 'failed')
        self.assertEqual(self.head(), old)
        self.assertNotIn('tests', self.sh.markers())
        self.assertIn('backup', self.notifier.sent[0][1])

    def test_an_unhealthy_controller_rolls_the_code_back(self):
        old = self.head()
        new = self.publish({'app/scripts/controller.py': 'v2'})
        self.health = [False] * 40 + [True]                       # never healthy in time, then fine after rollback
        self.assertEqual(self.deployer.run(), 'rolled-back')
        self.assertEqual(self.head(), old)
        self.assertEqual(self.restarts(), [['keezer'], ['keezer']])   # restarted, then restarted on the old code
        [(title, message, _)] = self.notifier.sent
        self.assertEqual(title, 'Keezer update rolled back')
        self.assertIn('did not report healthy', message)
        self.assertIn('healthy again', message)
        self.assertEqual(self.deployer.run(), 'skipped')              # not retried for the same commit

    def test_a_failed_restart_rolls_back(self):
        old = self.head()
        self.publish({'app/templates/index.html': 'v2'})
        self.sh.results['restart'] = (1, 'Failed to restart keezer-web.service')
        self.assertEqual(self.deployer.run(), 'rolled-back')
        self.assertEqual(self.head(), old)

    def test_a_failed_migration_rolls_back_the_code_without_restarting(self):
        old = self.head()
        self.publish({'migrations/versions/abc_x.py': 'm'})
        self.sh.results['migrate'] = (1, 'sqlite3.OperationalError')
        self.assertEqual(self.deployer.run(), 'rolled-back')
        self.assertEqual(self.head(), old)
        self.assertEqual(self.restarts(), [])                          # the running services never saw the new code
        self.assertIn('migration failed', self.notifier.sent[0][1])

    def test_a_failed_requirements_install_rolls_back(self):
        old = self.head()
        self.publish({'requires.txt': 'flask\nnewthing'})
        self.sh.sequences['pip'] = [(0, 'ok'), (1, 'no network')]       # the test copy works; the live install fails
        self.assertEqual(self.deployer.run(), 'rolled-back')
        self.assertEqual(self.head(), old)
        self.assertEqual(self.restarts(), [])
        self.assertIn('requirements failed', self.notifier.sent[0][1])

    # --- refusing to touch a checkout it does not own ---

    def test_local_changes_to_tracked_files_are_refused(self):
        self.publish({'README.md': 'v2'})
        self.write(self.pi, {'README.md': 'edited on the Pi'})
        self.assertEqual(self.deployer.run(), 'failed')
        self.assertEqual(self.sh.markers(), [])
        self.assertIn('local changes', self.notifier.sent[0][1])

    def test_untracked_files_do_not_matter(self):
        self.publish({'README.md': 'v2'})
        self.write(self.pi, {'app/scripts/lager_history.txt': 'scratch notes'})
        self.assertEqual(self.deployer.run(), 'deployed')

    def test_a_checkout_with_its_own_commits_is_refused(self):
        self.write(self.pi, {'extra.txt': 'x'})
        git(self.pi, 'add', '-A')
        git(self.pi, 'commit', '-m', 'local only')
        self.publish({'README.md': 'v2'})
        self.assertEqual(self.deployer.run(), 'failed')
        self.assertIn('cannot be fast-forwarded', self.notifier.sent[0][1])

    def test_another_branch_is_refused(self):
        git(self.pi, 'checkout', '-b', 'experiment')
        self.publish({'README.md': 'v2'})
        self.assertEqual(self.deployer.run(), 'failed')
        self.assertIn("'experiment'", self.notifier.sent[0][1])

    # --- the rest ---

    def test_dry_run_changes_nothing(self):
        old = self.head()
        self.publish({'app/scripts/controller.py': 'v2'})
        self.assertEqual(self.deployer.run(dry_run=True), 'dry-run')
        self.assertEqual(self.head(), old)
        self.assertEqual(self.sh.markers(), [])
        self.assertEqual(self.notifier.sent, [])

    def test_only_one_deploy_runs_at_a_time(self):
        self.publish({'README.md': 'v2'})
        with open(self.deployer.lock_path, 'w') as other:
            fcntl.flock(other, fcntl.LOCK_EX)
            self.assertEqual(self.deployer.run(), 'busy')
        self.assertEqual(self.sh.markers(), [])

    def test_a_notification_failure_does_not_break_the_deploy(self):
        class Broken(object):
            def send(self, *a, **k):
                raise RuntimeError('ntfy is down')
        self.deployer.notifier = Broken()
        self.publish({'README.md': 'v2'})
        self.assertEqual(self.deployer.run(), 'deployed')

    def test_works_without_a_notifier(self):
        self.deployer.notifier = None
        self.publish({'README.md': 'v2'})
        self.assertEqual(self.deployer.run(), 'deployed')


if __name__ == '__main__':
    unittest.main()
