# -*- coding: utf-8 -*-
"""The addon's stored logins: several users, one of them current. Kodi-free; the addon gives it a
file under its userdata profile.

Each user keeps its own session token, so switching users needs no new login."""
import json
import os
from dataclasses import asdict, dataclass
from typing import List, Optional


@dataclass
class Account:
    username: str
    password: str
    name: str = ''  # what menus and dialogs call this user
    token: str = ''  # the DAO's opaque session token

    @property
    def label(self):
        return self.name or self.username


class AccountStore:
    """Loaded from (and saved straight back to) one JSON file. Each Kodi navigation is a fresh process,
    so a store lives only as long as one invocation."""

    def __init__(self, path):
        self.path = path
        self.accounts: List[Account] = []
        self.current_username: Optional[str] = None
        self.existed = os.path.exists(path)
        if self.existed:
            self._load()

    def _load(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return  # unreadable: start empty rather than break every listing
        for a in data.get('accounts') or []:
            if a.get('username'):
                self.accounts.append(Account(username=a['username'], password=a.get('password') or '',
                                             name=a.get('name') or '', token=a.get('token') or ''))
        self.current_username = data.get('current')

    def save(self):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'current': self.current_username, 'accounts': [asdict(a) for a in self.accounts]}, f, indent=1)
        os.replace(tmp, self.path)  # atomic, so a crash can't leave half a file
        self.existed = True

    def find(self, username) -> Optional[Account]:
        return next((a for a in self.accounts if a.username == username), None)

    def current(self) -> Optional[Account]:
        """The current user; the first one when the saved choice is gone."""
        return self.find(self.current_username) or (self.accounts[0] if self.accounts else None)

    def others(self) -> List[Account]:
        current = self.current()
        return [a for a in self.accounts if a is not current]

    def add(self, account):
        """Add a user and make them current. An existing username is replaced (a new password), keeping
        its name unless a new one is given."""
        old = self.find(account.username)
        if old:
            account.name = account.name or old.name
            self.accounts[self.accounts.index(old)] = account
        else:
            self.accounts.append(account)
        self.current_username = account.username
        self.save()

    def remove(self, username):
        self.accounts = [a for a in self.accounts if a.username != username]
        if self.current_username == username:
            self.current_username = self.accounts[0].username if self.accounts else None
        self.save()

    def select(self, username):
        if self.find(username):
            self.current_username = username
            self.save()

    def set_token(self, username, token):
        a = self.find(username)
        if a:
            a.token = token or ''
            self.save()

    def set_name(self, username, name):
        a = self.find(username)
        if a:
            a.name = name
            self.save()


def migrate_legacy(store, username, password, token='', name=''):
    """The addon used to keep one login in its settings. On the first run with a store, that login becomes
    the first user. Returns True when it migrated (so the caller can blank the old settings)."""
    if store.existed or not username or not password:
        return False
    store.add(Account(username=username, password=password, name=name, token=token or ''))
    return True
