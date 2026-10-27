"""Local administration: python -m api.auth.manage create-user|reset-password NAME."""

import argparse
import getpass
import sqlite3

from ..config import Settings
from ..db.auth_repository import auth_repository
from ..db.database import Database, create_database
from ..db.errors import StorageConflict
from .security import hash_password


def set_user_password(database: Database, username: str, password: str, *, create: bool):
    username = username.strip()
    if not 3 <= len(username) <= 64:
        raise ValueError("用户名长度须为 3–64 个字符。")
    if not 12 <= len(password) <= 256:
        raise ValueError("新密码长度须为 12–256 个字符；迁入的已有密码继续有效。")
    repository = auth_repository(database)
    user = repository.get_user_by_username(username)
    if create:
        if user:
            raise ValueError("用户已存在。")
        try:
            repository.create_user(username, hash_password(password))
        except (sqlite3.IntegrityError, StorageConflict) as exc:
            raise ValueError("用户已存在。") from exc
    else:
        if not user:
            raise ValueError("用户不存在。")
        repository.reset_password(user["id"], hash_password(password))


def main():
    parser = argparse.ArgumentParser(description="SeeAlpha 本地用户管理")
    parser.add_argument("action", choices=["create-user", "reset-password"])
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("密码: ")
    if password != getpass.getpass("确认密码: "):
        raise SystemExit("两次密码不一致。")
    database = create_database(Settings.from_environment())
    database.initialize()
    try:
        set_user_password(database, args.username, password, create=args.action == "create-user")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        database.close()
    print("用户已创建。" if args.action == "create-user" else "密码已重置，旧会话已撤销。")


if __name__ == "__main__":
    main()
