"""Backend-independent repositories over SQLAlchemy ORM models."""

import time

from sqlalchemy import delete, func, select, update

from .auth_repository import provision_user_tenant
from .models import ExchangeAccount, LoginFailure, OlapState, Session, User


def row(model, *fields):
    return {field: getattr(model, field) for field in fields} if model else None


_USER = ("id", "username", "password_hash", "created_at")
_ACCOUNT = (
    "id",
    "tenant_id",
    "exchange",
    "account_name",
    "environment",
    "credentials_encrypted",
    "market_symbols_json",
    "last_test_status",
    "last_tested_at",
    "created_at",
    "updated_at",
)


class ORMAuthRepository:
    def __init__(self, database):
        self.database = database

    def create_user(self, username, password_hash):
        now = int(time.time())
        with self.database.session(write=True) as session:
            user = User(username=username, password_hash=password_hash, created_at=now)
            session.add(user)
            session.flush()
            created = {"id": user.id, "username": username, "created_at": now}
        provision_user_tenant(self.database, created)
        return created

    def get_user_by_id(self, user_id):
        with self.database.session() as session:
            user = session.get(User, user_id)
            return row(user, "id", "username", "created_at")

    def list_users(self):
        with self.database.session() as session:
            users = session.scalars(select(User).order_by(User.id)).all()
            return [row(user, "id", "username", "created_at") for user in users]

    def get_user_by_username(self, username):
        with self.database.session() as session:
            user = session.scalar(
                select(User).where(func.lower(User.username) == func.lower(username))
            )
            return row(user, *_USER)

    def update_password(self, user_id, password_hash):
        with self.database.session(write=True) as session:
            session.execute(
                update(User).where(User.id == user_id).values(password_hash=password_hash)
            )

    def reset_password(self, user_id, password_hash):
        with self.database.session(write=True) as session:
            session.execute(
                update(User).where(User.id == user_id).values(password_hash=password_hash)
            )
            session.execute(delete(Session).where(Session.user_id == user_id))

    def create_session(self, user_id, token_hash, expires_at):
        now = int(time.time())
        with self.database.session(write=True) as session:
            session.execute(delete(Session).where(Session.expires_at <= now))
            session.add(
                Session(
                    token_hash=token_hash, user_id=user_id, created_at=now, expires_at=expires_at
                )
            )

    def get_user_for_session(self, token_hash):
        now = int(time.time())
        with self.database.session() as session:
            result = (
                session.execute(
                    select(
                        User.id, User.username, User.created_at, Session.expires_at,
                        Session.impersonate_user_id,
                    )
                    .join(Session, Session.user_id == User.id)
                    .where(Session.token_hash == token_hash, Session.expires_at > now)
                )
                .mappings()
                .first()
            )
            return dict(result) if result else None

    def set_impersonation(self, token_hash, user_id):
        with self.database.session(write=True) as session:
            session.execute(
                update(Session)
                .where(Session.token_hash == token_hash)
                .values(impersonate_user_id=user_id)
            )

    def delete_session(self, token_hash):
        with self.database.session(write=True) as session:
            session.execute(delete(Session).where(Session.token_hash == token_hash))

    def record_login_failure(self, identity_hash):
        with self.database.session(write=True) as session:
            session.add(LoginFailure(identity_hash=identity_hash, attempted_at=int(time.time())))

    def clear_login_failures(self, identity_hash):
        with self.database.session(write=True) as session:
            session.execute(delete(LoginFailure).where(LoginFailure.identity_hash == identity_hash))

    def is_login_rate_limited(self, identity_hash, *, limit=5, window_seconds=15 * 60):
        cutoff = int(time.time()) - window_seconds
        with self.database.session(write=True) as session:
            session.execute(delete(LoginFailure).where(LoginFailure.attempted_at < cutoff))
            count = session.scalar(
                select(func.count())
                .select_from(LoginFailure)
                .where(
                    LoginFailure.identity_hash == identity_hash, LoginFailure.attempted_at >= cutoff
                )
            )
            return count >= limit


class ORMExchangeRepository:
    def __init__(self, database):
        self.database = database

    def list_exchange_accounts(self, tenant_id=None):
        with self.database.session() as session:
            statement = select(ExchangeAccount).order_by(
                ExchangeAccount.exchange,
                func.lower(ExchangeAccount.account_name),
                ExchangeAccount.id,
            )
            if tenant_id is not None:
                statement = statement.where(ExchangeAccount.tenant_id == tenant_id)
            accounts = session.scalars(statement).all()
            return [row(account, *_ACCOUNT) for account in accounts]

    def get_exchange_account(self, account_id):
        with self.database.session() as session:
            return row(session.get(ExchangeAccount, account_id), *_ACCOUNT)

    def create_exchange_account(self, **values):
        now = int(time.time())
        with self.database.session(write=True) as session:
            account = ExchangeAccount(**values, created_at=now, updated_at=now)
            session.add(account)
            session.flush()
            return row(account, *_ACCOUNT)

    def update_exchange_account(self, account_id, **values):
        with self.database.session(write=True) as session:
            account = session.get(ExchangeAccount, account_id)
            if account is None:
                return None
            for key, value in values.items():
                setattr(account, key, value)
            account.last_test_status = account.last_tested_at = None
            account.updated_at = int(time.time())
            session.flush()
            return row(account, *_ACCOUNT)

    def update_exchange_account_test_result(self, account_id, *, succeeded):
        with self.database.session(write=True) as session:
            session.execute(
                update(ExchangeAccount)
                .where(ExchangeAccount.id == account_id)
                .values(
                    last_test_status="success" if succeeded else "failed",
                    last_tested_at=int(time.time()),
                )
            )

    def delete_exchange_account(self, account_id):
        with self.database.session(write=True) as session:
            return (
                session.execute(
                    delete(ExchangeAccount).where(ExchangeAccount.id == account_id)
                ).rowcount
                > 0
            )


class ORMOlapRepository:
    def __init__(self, database):
        self.database = database

    def initialize(self):
        pass  # The database migration owns the shared table.

    def _get(self, name):
        with self.database.session() as session:
            item = session.get(OlapState, name)
            return dict(item.value) if item else None

    def _put(self, name, values):
        value = {**values, "updated_at": int(time.time())}
        with self.database.session(write=True, lock_key="olap:" + name) as session:
            item = session.get(OlapState, name)
            if item is None:
                session.add(OlapState(name=name, value=value))
            else:
                item.value = value
        return value

    def get_olap_settings(self):
        return self._get("settings")

    def save_olap_settings(self, **values):
        return self._put("settings", values)

    def get_clickhouse_monitor_settings(self):
        return self._get("monitor")

    def save_clickhouse_monitor_settings(self, **values):
        return self._put("monitor", values)

    def get_clickhouse_monitor_cache(self):
        return self._get("cache")

    def save_clickhouse_monitor_cache(self, **values):
        self._put("cache", values)

    def delete_clickhouse_monitor_cache(self):
        with self.database.session(write=True, lock_key="olap:cache") as session:
            session.execute(delete(OlapState).where(OlapState.name == "cache"))
