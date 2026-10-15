"""SQLite project persistence; every mutation runs in one write transaction."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class ProjectNotFound(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS projects ("
                "id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create(self, project):
        with self._session() as connection:
            connection.execute(
                "INSERT INTO projects (id, body) VALUES (?, ?)",
                (project["id"], json.dumps(project, ensure_ascii=False)),
            )
        return project

    def get(self, project_id):
        with self._session() as connection:
            row = connection.execute(
                "SELECT body FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return json.loads(row[0])

    def list(self):
        with self._session() as connection:
            rows = connection.execute("SELECT body FROM projects").fetchall()
        projects = [json.loads(row[0]) for row in rows]
        return sorted(projects, key=lambda project: project["updated_at"], reverse=True)

    def update(self, project_id, mutate):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT body FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)
            project = json.loads(row[0])
            mutate(project)
            connection.execute(
                "UPDATE projects SET body = ? WHERE id = ?",
                (json.dumps(project, ensure_ascii=False), project_id),
            )
            connection.commit()
            return project
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
