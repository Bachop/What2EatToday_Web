"""Scoped adapter for the unchanged desktop domain service."""

from contextlib import contextmanager
from datetime import date, datetime

from .colors import option_color
from .dates import month_bounds
from .models import FoodOption, LunchRecord


class LunchRepository:
    def __init__(self, connection, scope_id):
        self.connection = connection
        self.scope_id = scope_id

    @contextmanager
    def transaction(self):
        # API owns the outer transaction, including membership resolution.
        if self.connection.in_transaction:
            yield
            return
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def options(self):
        return [FoodOption(r['id'], r['name'], r['weight'], r['color']) for r in
                self.connection.execute("SELECT * FROM options WHERE scope_id=? AND active=1 ORDER BY id", (self.scope_id,))]

    def insert_option(self, name, weight):
        item_id = self.connection.execute(
            "INSERT INTO options(scope_id,name,name_key,weight,color) VALUES(?,?,?,?,?)",
            (self.scope_id, name, name.casefold(), weight, '#000000')).lastrowid
        self.connection.execute("UPDATE options SET color=? WHERE id=?", (option_color(item_id), item_id))
        return item_id

    def update_weights(self, option_ids, weights):
        if len(option_ids) != len(weights):
            raise ValueError("Mismatched weights")
        self.connection.executemany("UPDATE options SET weight=? WHERE id=? AND scope_id=? AND active=1",
                                    [(weight, item, self.scope_id) for item, weight in zip(option_ids, weights)])

    def deactivate(self, option_ids):
        self.connection.executemany("UPDATE options SET active=0,weight=0 WHERE id=? AND scope_id=?",
                                    [(item, self.scope_id) for item in option_ids])

    @staticmethod
    def _record(r):
        return LunchRecord(date.fromisoformat(r['day']), r['option_id'], r['option_name'], r['color'],
                           r['rerolls'], datetime.fromisoformat(r['chosen_at']))

    def record(self, day):
        row = self.connection.execute("SELECT * FROM lunches WHERE scope_id=? AND day=?",
                                      (self.scope_id, day.isoformat())).fetchone()
        return self._record(row) if row else None

    def history_before(self, day):
        return [self._record(r) for r in self.connection.execute(
            "SELECT * FROM lunches WHERE scope_id=? AND day<? ORDER BY day", (self.scope_id, day.isoformat()))]

    def month(self, year, month):
        start, end = month_bounds(year, month)
        return [self._record(r) for r in self.connection.execute(
            "SELECT * FROM lunches WHERE scope_id=? AND day>=? AND day<? ORDER BY day",
            (self.scope_id, start.isoformat(), end.isoformat()))]

    def save_record(self, r):
        self.connection.execute("""INSERT INTO lunches VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(scope_id,day) DO UPDATE SET option_id=excluded.option_id,
            option_name=excluded.option_name,color=excluded.color,rerolls=excluded.rerolls,chosen_at=excluded.chosen_at""",
            (self.scope_id, r.day.isoformat(), r.option_id, r.option_name, r.color, r.rerolls, r.chosen_at.isoformat()))
