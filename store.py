# -*- coding: utf-8 -*-
"""SQLite 航班信息持久化与检索。"""
import sqlite3
import threading
import time
from datetime import datetime


SCHEMA = """
CREATE TABLE IF NOT EXISTS msgs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,           -- 接收时刻 epoch
    ts_text   TEXT NOT NULL,           -- 本地时间文本
    flight    TEXT,
    reg       TEXT,
    lat       REAL,
    lon       REAL,
    heading   REAL,
    altitude  INTEGER,
    speed     INTEGER,
    mode      INTEGER,
    crc_ok    INTEGER,
    raw_hex   TEXT,
    text      TEXT
);
CREATE INDEX IF NOT EXISTS idx_msgs_ts     ON msgs(ts);
CREATE INDEX IF NOT EXISTS idx_msgs_flight ON msgs(flight);
"""


class FlightStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL;")
        self._con.executescript(SCHEMA)
        self._con.commit()

    # ---------- 写入 ----------
    def add(self, fr):
        with self._lock:
            self._con.execute(
                "INSERT INTO msgs(ts,ts_text,flight,reg,lat,lon,heading,altitude,"
                "speed,mode,crc_ok,raw_hex,text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fr.ts, datetime.fromtimestamp(fr.ts).strftime("%Y-%m-%d %H:%M:%S"),
                 fr.flight or None, fr.reg or None, fr.lat, fr.lon, fr.heading,
                 fr.altitude, fr.speed, fr.mode, int(fr.crc_ok),
                 fr.raw.hex(), (fr.text or "")[:500]))
            self._con.commit()

    # ---------- 检索 ----------
    def search(self, flight=None, t_from=None, t_to=None, limit=500):
        q = "SELECT id,ts_text,flight,reg,lat,lon,heading,altitude,mode,crc_ok,text FROM msgs WHERE 1=1"
        args = []
        if flight:
            q += " AND (UPPER(flight) LIKE ? OR UPPER(reg) LIKE ?)"
            like = f"%{flight.upper()}%"
            args += [like, like]
        if t_from:
            q += " AND ts >= ?"; args.append(t_from)
        if t_to:
            q += " AND ts <= ?"; args.append(t_to)
        q += " ORDER BY ts DESC LIMIT ?"; args.append(int(limit))
        with self._lock:
            return self._con.execute(q, args).fetchall()

    def flight_track(self, flight, minutes=120):
        t0 = time.time() - minutes * 60
        with self._lock:
            rows = self._con.execute(
                "SELECT ts,lat,lon,heading FROM msgs WHERE UPPER(flight)=? "
                "AND lat IS NOT NULL AND ts>=? ORDER BY ts",
                (flight.upper(), t0)).fetchall()
        return rows

    def count(self):
        with self._lock:
            return self._con.execute("SELECT COUNT(*) FROM msgs").fetchone()[0]

    def close(self):
        try:
            self._con.close()
        except Exception:
            pass
