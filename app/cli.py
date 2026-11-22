"""Explicit release migration and secure bootstrap; never creates a default account."""
import argparse
import asyncio
import getpass
import os
from pathlib import Path
import re
import sys
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from app.config import Settings
from app.db import database, User
from app.security import hash_password


async def create_user(username: str, role: str, password: str):
    engine, sessions = database(Settings())
    try:
        async with sessions() as session:
            session.add(User(username=username, role=role, password_hash=hash_password(password)))
            await session.commit()
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description='AIQA administration')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('migrate', help='Apply reviewed Alembic migrations to head')
    create = commands.add_parser('create-user', help='Create a user; password from BOOTSTRAP_PASSWORD or secure prompt')
    create.add_argument('--username', required=True)
    create.add_argument('--role', choices=('admin','user','readonly'), default='user')
    args = parser.parse_args()
    try:
        if args.command == 'migrate':
            config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
            command.upgrade(config, 'head')
            print('Migrations applied.')
        else:
            if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,128}', args.username):
                parser.error('Username must be 1-128 letters, digits or _.@-')
            password = os.environ.get('BOOTSTRAP_PASSWORD')
            if password is None:
                if not sys.stdin.isatty():
                    parser.error('Set BOOTSTRAP_PASSWORD or use an interactive terminal')
                password = getpass.getpass('New password: ')
                if password != getpass.getpass('Confirm password: '):
                    parser.error('Password confirmation does not match')
            if not 12 <= len(password) <= 1024:
                parser.error('Password must contain 12-1024 characters')
            asyncio.run(create_user(args.username, args.role, password))
            print('User created.')
    except IntegrityError:
        print('User creation failed: username already exists or violates schema.', file=sys.stderr)
        return 1
    except (SQLAlchemyError, OSError):
        print('Database operation failed; verify database availability and migration state.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
