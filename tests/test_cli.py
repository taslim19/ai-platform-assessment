import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(tmp_path, *arguments, password='secure-test-password'):
    env = dict(os.environ, DATABASE_URL=f'sqlite+aiosqlite:///{tmp_path}/cli.db',
               JWT_SECRET='test-only-cli-secret-12345678901234567890')
    if password is not None:
        env['BOOTSTRAP_PASSWORD'] = password
    else:
        env.pop('BOOTSTRAP_PASSWORD', None)
    return subprocess.run([sys.executable, '-m', 'app.cli', *arguments], env=env,
                          cwd=ROOT, capture_output=True, text=True, input='', timeout=20)


def test_migration_cli_idempotency_and_bootstrap(tmp_path):
    first = run_cli(tmp_path, 'migrate')
    assert first.returncode == 0, first.stderr
    assert run_cli(tmp_path, 'migrate').returncode == 0
    result = run_cli(tmp_path, 'create-user', '--username', 'admin', '--role', 'admin')
    assert result.returncode == 0, result.stderr
    assert 'secure-test-password' not in result.stdout + result.stderr
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(tmp_path/'cli.db')) as db:
        row = db.execute('SELECT username,role,password_hash FROM users').fetchone()
        assert row[:2] == ('admin','admin')
        assert row[2].startswith('$argon2id$')
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '0001'
    duplicate = run_cli(tmp_path, 'create-user', '--username', 'admin', '--role', 'admin')
    assert duplicate.returncode != 0
    assert 'Traceback' not in duplicate.stderr


def test_bootstrap_password_minimum(tmp_path):
    assert run_cli(tmp_path,'migrate').returncode == 0
    result = run_cli(tmp_path,'create-user','--username','admin','--role','admin',password='short')
    assert result.returncode != 0
    assert 'short' not in result.stdout + result.stderr
