#!/usr/bin/env bash
# setup-workspace.sh — Create a temp workspace for codeact tests
# Outputs the workspace path on stdout
set -euo pipefail

WORKSPACE=$(mktemp -d /tmp/codeact-test-XXXX)

# --- src/ directory with Python files ---
mkdir -p "$WORKSPACE/src"

cat > "$WORKSPACE/src/app.py" << 'PYEOF'
"""Main application module."""
import os
import sys
from pathlib import Path


# TODO: Add proper logging configuration
def main():
    """Entry point for the application."""
    config = load_config()
    # TODO: Validate config before using
    server = create_server(config)
    server.run()


def load_config():
    """Load configuration from environment."""
    return {
        "host": os.environ.get("APP_HOST", "0.0.0.0"),
        "port": int(os.environ.get("APP_PORT", "8080")),
        "debug": os.environ.get("APP_DEBUG", "false").lower() == "true",
    }


def create_server(config):
    """Create and configure the server."""
    # TODO: Support HTTPS configuration
    return Server(config)


class Server:
    def __init__(self, config):
        self.config = config
        self.routes = {}

    def add_route(self, path, handler):
        self.routes[path] = handler

    def run(self):
        # TODO: Implement graceful shutdown
        print(f"Server running on {self.config['host']}:{self.config['port']}")

    def health_check(self):
        return {"status": "ok"}


def parse_request(raw):
    """Parse an HTTP request string."""
    lines = raw.split("\n")
    method, path, _ = lines[0].split(" ")
    return {"method": method, "path": path}


def format_response(status, body):
    """Format an HTTP response."""
    return f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n\r\n{body}"


# TODO: Add request rate limiting
def handle_request(request):
    """Handle an incoming request."""
    if request["method"] == "GET":
        return format_response("200 OK", '{"message": "hello"}')
    return format_response("405 Method Not Allowed", "")


if __name__ == "__main__":
    main()
PYEOF

cat > "$WORKSPACE/src/utils.py" << 'PYEOF'
"""Utility functions."""
import re
import json
from datetime import datetime


def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text


def timestamp():
    return datetime.now().isoformat()


def deep_merge(base, override):
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def truncate(text, max_length=100):
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def parse_csv_line(line):
    return [field.strip() for field in line.split(",")]


def validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def chunk_list(lst, size):
    return [lst[i:i + size] for i in range(0, len(lst), size)]
PYEOF

cat > "$WORKSPACE/src/models.py" << 'PYEOF'
"""Data models."""
import os
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class User:
    id: int
    name: str
    email: str
    role: str = "user"
    active: bool = True

    def display_name(self):
        return f"{self.name} ({self.role})"


@dataclass
class Project:
    id: int
    name: str
    owner: User
    members: List[User] = field(default_factory=list)
    description: Optional[str] = None

    def add_member(self, user):
        if user not in self.members:
            self.members.append(user)

    def member_count(self):
        return len(self.members) + 1  # +1 for owner


@dataclass
class Task:
    id: int
    title: str
    project: Project
    assignee: Optional[User] = None
    status: str = "open"
    priority: int = 0

    def assign(self, user):
        self.assignee = user

    def close(self):
        self.status = "closed"

    def is_overdue(self):
        return False  # TODO: implement with due dates
PYEOF

cat > "$WORKSPACE/src/api.py" << 'PYEOF'
"""API endpoint handlers."""
import os
import json


# TODO: Add authentication middleware
def get_users(db):
    """Get all users from the database."""
    users = db.query("SELECT * FROM users WHERE active = 1")
    return json.dumps(users)


def create_user(db, data):
    """Create a new user."""
    required = ["name", "email"]
    for field in required:
        if field not in data:
            return {"error": f"Missing field: {field}"}, 400
    db.insert("users", data)
    return {"status": "created"}, 201


def get_projects(db, user_id=None):
    """Get projects, optionally filtered by user."""
    if user_id:
        return db.query(f"SELECT * FROM projects WHERE owner_id = {user_id}")
    return db.query("SELECT * FROM projects")


def health(db):
    """Health check endpoint."""
    try:
        db.query("SELECT 1")
        return {"status": "healthy", "database": "connected"}
    except Exception:
        return {"status": "unhealthy", "database": "disconnected"}


def search(db, query, limit=10):
    """Search across all resources."""
    results = []
    for table in ["users", "projects", "tasks"]:
        rows = db.query(f"SELECT * FROM {table} WHERE name LIKE '%{query}%' LIMIT {limit}")
        results.extend(rows)
    return results
PYEOF

cat > "$WORKSPACE/src/config.py" << 'PYEOF'
"""Configuration management."""
import os
import json
from pathlib import Path


DEFAULT_CONFIG = {
    "app": {
        "name": "myapp",
        "version": "1.0.0",
        "debug": False,
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "workers": 4,
    },
    "database": {
        "url": "sqlite:///app.db",
        "pool_size": 5,
    },
    "logging": {
        "level": "INFO",
        "format": "%(asctime)s %(levelname)s %(message)s",
    },
}


def load_config(path=None):
    """Load config from file, falling back to defaults."""
    config = DEFAULT_CONFIG.copy()
    if path and Path(path).exists():
        with open(path) as f:
            overrides = json.load(f)
        config.update(overrides)
    # Environment overrides
    if os.environ.get("APP_DEBUG"):
        config["app"]["debug"] = True
    if os.environ.get("APP_PORT"):
        config["server"]["port"] = int(os.environ["APP_PORT"])
    return config


def save_config(config, path):
    """Save config to file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
PYEOF

# --- config/ directory with JSON files ---
mkdir -p "$WORKSPACE/config"

cat > "$WORKSPACE/config/settings.json" << 'EOF'
{
  "app_name": "test-app",
  "version": "2.1.0",
  "features": {
    "dark_mode": true,
    "notifications": true,
    "beta_features": false
  }
}
EOF

cat > "$WORKSPACE/config/database.json" << 'EOF'
{
  "host": "localhost",
  "port": 5432,
  "name": "testdb",
  "pool_size": 10,
  "ssl": true
}
EOF

cat > "$WORKSPACE/config/broken.json" << 'EOF'
{
  "key": "value",
  "missing_closing_bracket": [1, 2, 3
  "another_key": true
}
EOF

# --- tests/ directory ---
mkdir -p "$WORKSPACE/tests"

cat > "$WORKSPACE/tests/test_app.py" << 'PYEOF'
"""Tests for app module."""
import pytest


def test_load_config():
    from src.app import load_config
    config = load_config()
    assert "host" in config
    assert "port" in config


def test_parse_request():
    from src.app import parse_request
    req = parse_request("GET /api/users HTTP/1.1\nHost: localhost")
    assert req["method"] == "GET"
    assert req["path"] == "/api/users"


def test_format_response():
    from src.app import format_response
    resp = format_response("200 OK", "hello")
    assert "200 OK" in resp
    assert "hello" in resp
PYEOF

cat > "$WORKSPACE/tests/test_utils.py" << 'PYEOF'
"""Tests for utils module."""


def test_slugify():
    from src.utils import slugify
    assert slugify("Hello World") == "hello-world"
    assert slugify("  spaces  ") == "spaces"


def test_truncate():
    from src.utils import truncate
    assert truncate("short") == "short"
    assert len(truncate("a" * 200)) <= 100


def test_validate_email():
    from src.utils import validate_email
    assert validate_email("user@example.com")
    assert not validate_email("invalid")
PYEOF

# --- README.md ---
cat > "$WORKSPACE/README.md" << 'EOF'
# Test Project

A sample project for testing codeact functionality.

## Structure

- `src/` — Source code
- `config/` — Configuration files
- `tests/` — Test files

## Setup

```bash
pip install -r requirements.txt
python -m src.app
```
EOF

# --- src/handlers/ ---
mkdir -p "$WORKSPACE/src/handlers"

cat > "$WORKSPACE/src/handlers/__init__.py" << 'PYEOF'
"""HTTP request handlers."""
PYEOF

cat > "$WORKSPACE/src/handlers/user_handler.py" << 'PYEOF'
"""User endpoint handlers."""
from src.models import User
from src.services.user_service import get_user, list_users


def handle_get_users(request):
    """List all active users."""
    users = list_users(active_only=True)
    return {"users": users, "count": len(users)}


def handle_get_user(request, user_id):
    user = get_user(user_id)
    if not user:
        return {"error": "Not found"}, 404
    return user


def handle_create_user(request):
    """Create a new user from request data."""
    data = request.get("body", {})
    # TODO: Add input validation
    if not data.get("email"):
        return {"error": "Email required"}, 400
    user = User(id=0, name=data["name"], email=data["email"])
    return {"id": user.id, "status": "created"}, 201


def handle_update_user(request, user_id):
    data = request.get("body", {})
    # TODO: Validate update fields
    return {"id": user_id, "status": "updated"}


def handle_delete_user(request, user_id):
    """Delete a user by ID."""
    # TODO: Add soft-delete support
    return {"status": "deleted"}, 204
PYEOF

cat > "$WORKSPACE/src/handlers/project_handler.py" << 'PYEOF'
"""Project endpoint handlers."""
from src.models import Project
from src.services.project_service import get_project, list_projects


def handle_list_projects(request):
    """Return all projects."""
    return {"projects": list_projects()}


def handle_get_project(request, project_id):
    project = get_project(project_id)
    if not project:
        return {"error": "Not found"}, 404
    return project


def handle_create_project(request):
    """Create a new project."""
    data = request.get("body", {})
    # TODO: Validate required fields
    return {"status": "created"}, 201


def handle_update_project(request, project_id):
    data = request.get("body", {})
    return {"id": project_id, "status": "updated"}


def handle_archive_project(request, project_id):
    """Archive a project instead of deleting."""
    # TODO: Notify project members on archive
    return {"status": "archived"}
PYEOF

cat > "$WORKSPACE/src/handlers/task_handler.py" << 'PYEOF'
"""Task management handlers."""
from src.models import Task


def handle_list_tasks(request, project_id):
    """List tasks for a project."""
    # TODO: Add pagination support
    return {"tasks": [], "project_id": project_id}


def handle_get_task(request, task_id):
    return {"task_id": task_id}


def handle_create_task(request, project_id):
    data = request.get("body", {})
    # TODO: Validate priority range 0-5
    return {"status": "created"}, 201


def handle_assign_task(request, task_id, user_id):
    """Assign a task to a user."""
    return {"task_id": task_id, "assignee": user_id}


def handle_close_task(request, task_id):
    # TODO: Check if task has open subtasks before closing
    return {"task_id": task_id, "status": "closed"}
PYEOF

cat > "$WORKSPACE/src/handlers/auth_handler.py" << 'PYEOF'
"""Authentication handlers."""
import os
import json


def handle_login(request):
    """Authenticate user and return token."""
    data = request.get("body", {})
    if not data.get("email") or not data.get("password"):
        return {"error": "Credentials required"}, 401
    # TODO: Implement proper password hashing
    return {"token": "fake-jwt-token"}


def handle_logout(request):
    return {"status": "logged out"}


def handle_refresh_token(request):
    """Refresh an expired JWT token."""
    # TODO: Validate refresh token expiry
    return {"token": "new-fake-jwt-token"}


def handle_reset_password(request):
    data = request.get("body", {})
    if not data.get("email"):
        return {"error": "Email required"}, 400
    # TODO: Send actual reset email
    return {"status": "reset email sent"}
PYEOF

# --- src/middleware/ ---
mkdir -p "$WORKSPACE/src/middleware"

cat > "$WORKSPACE/src/middleware/__init__.py" << 'PYEOF'
"""Request/response middleware."""
PYEOF

cat > "$WORKSPACE/src/middleware/auth.py" << 'PYEOF'
"""Authentication middleware."""
import os


SECRET_KEY = os.environ.get("JWT_SECRET", "dev-secret")


def authenticate(request):
    """Verify JWT token in Authorization header."""
    token = request.get("headers", {}).get("Authorization", "")
    if not token.startswith("Bearer "):
        return None
    # TODO: Actually verify JWT signature
    return {"user_id": 1, "role": "user"}


def require_role(role):
    def decorator(handler):
        def wrapper(request, *args, **kwargs):
            user = authenticate(request)
            if not user or user.get("role") != role:
                return {"error": "Forbidden"}, 403
            return handler(request, *args, **kwargs)
        return wrapper
    return decorator


def hash_password(password):
    # TODO: Use bcrypt instead of this placeholder
    return f"hashed_{password}"
PYEOF

cat > "$WORKSPACE/src/middleware/logging_mw.py" << 'PYEOF'
"""Request logging middleware."""
import time
from datetime import datetime


def log_request(request):
    """Log incoming request details."""
    method = request.get("method", "?")
    path = request.get("path", "?")
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] {method} {path}")


def log_response(request, response, duration_ms):
    status = response[1] if isinstance(response, tuple) else 200
    print(f"  -> {status} ({duration_ms}ms)")


def timing_middleware(handler):
    def wrapper(request, *args, **kwargs):
        start = time.time()
        response = handler(request, *args, **kwargs)
        duration = (time.time() - start) * 1000
        log_response(request, response, duration)
        return response
    return wrapper
PYEOF

cat > "$WORKSPACE/src/middleware/cors.py" << 'PYEOF'
"""CORS handling middleware."""

ALLOWED_ORIGINS = ["http://localhost:3000", "https://app.example.com"]


def cors_headers(origin):
    """Generate CORS headers for the given origin."""
    if origin in ALLOWED_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }
    return {}


def cors_middleware(handler):
    def wrapper(request, *args, **kwargs):
        origin = request.get("headers", {}).get("Origin", "")
        response = handler(request, *args, **kwargs)
        headers = cors_headers(origin)
        return response, headers
    return wrapper
PYEOF

cat > "$WORKSPACE/src/middleware/rate_limit.py" << 'PYEOF'
"""Rate limiting middleware."""
import time


# In-memory store (not suitable for production)
_request_counts = {}


def check_rate_limit(client_ip, max_requests=100, window_seconds=60):
    """Check if client has exceeded rate limit."""
    now = time.time()
    key = f"{client_ip}:{int(now / window_seconds)}"
    _request_counts[key] = _request_counts.get(key, 0) + 1
    return _request_counts[key] <= max_requests


def rate_limit_middleware(handler):
    def wrapper(request, *args, **kwargs):
        ip = request.get("client_ip", "unknown")
        if not check_rate_limit(ip):
            return {"error": "Too many requests"}, 429
        return handler(request, *args, **kwargs)
    return wrapper


def reset_counts():
    # TODO: Add automatic cleanup of old window entries
    _request_counts.clear()
PYEOF

# --- src/services/ ---
mkdir -p "$WORKSPACE/src/services"

cat > "$WORKSPACE/src/services/__init__.py" << 'PYEOF'
"""Business logic services."""
PYEOF

cat > "$WORKSPACE/src/services/user_service.py" << 'PYEOF'
"""User business logic."""
from src.models import User


_users_db = []


def get_user(user_id):
    for u in _users_db:
        if u.id == user_id:
            return u
    return None


def list_users(active_only=False):
    """Return all users, optionally filtered by active status."""
    if active_only:
        return [u for u in _users_db if u.active]
    return list(_users_db)


def create_user(name, email, role="user"):
    """Create and store a new user."""
    user = User(id=len(_users_db) + 1, name=name, email=email, role=role)
    _users_db.append(user)
    return user


def deactivate_user(user_id):
    user = get_user(user_id)
    if user:
        user.active = False
    return user


def search_users(query):
    # TODO: Add fuzzy matching
    return [u for u in _users_db if query.lower() in u.name.lower()]
PYEOF

cat > "$WORKSPACE/src/services/project_service.py" << 'PYEOF'
"""Project business logic."""
from src.models import Project


_projects_db = []


def get_project(project_id):
    """Fetch a single project by ID."""
    for p in _projects_db:
        if p.id == project_id:
            return p
    return None


def list_projects(owner_id=None):
    if owner_id:
        return [p for p in _projects_db if p.owner.id == owner_id]
    return list(_projects_db)


def create_project(name, owner, description=None):
    """Create a new project."""
    project = Project(
        id=len(_projects_db) + 1,
        name=name, owner=owner, description=description,
    )
    _projects_db.append(project)
    return project


def archive_project(project_id):
    # TODO: Cascade archive to project tasks
    project = get_project(project_id)
    return project
PYEOF

cat > "$WORKSPACE/src/services/email_service.py" << 'PYEOF'
"""Email sending service."""
import os


SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))


def send_email(to, subject, body):
    """Send an email message."""
    # TODO: Implement actual SMTP connection
    print(f"Sending to {to}: {subject}")
    return True


def send_welcome_email(user):
    return send_email(
        user.email,
        "Welcome!",
        f"Hello {user.name}, welcome to the platform.",
    )


def send_password_reset(email, reset_url):
    # TODO: Add email template rendering
    return send_email(email, "Password Reset", f"Reset here: {reset_url}")


def send_notification(user, message):
    """Send a notification email."""
    return send_email(user.email, "Notification", message)
PYEOF

cat > "$WORKSPACE/src/services/cache_service.py" << 'PYEOF'
"""In-memory cache service."""
import time


_cache = {}


def get(key):
    entry = _cache.get(key)
    if entry is None:
        return None
    if entry["expires"] and time.time() > entry["expires"]:
        del _cache[key]
        return None
    return entry["value"]


def set(key, value, ttl_seconds=300):
    """Store a value with optional TTL."""
    _cache[key] = {
        "value": value,
        "expires": time.time() + ttl_seconds if ttl_seconds else None,
    }


def delete(key):
    _cache.pop(key, None)


def clear():
    """Clear the entire cache."""
    # TODO: Add cache statistics tracking before clear
    _cache.clear()


def cache_size():
    return len(_cache)
PYEOF

# --- src/db/ ---
mkdir -p "$WORKSPACE/src/db"

cat > "$WORKSPACE/src/db/__init__.py" << 'PYEOF'
"""Database layer."""
PYEOF

cat > "$WORKSPACE/src/db/connection.py" << 'PYEOF'
"""Database connection management."""
import os


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///app.db")
_pool = []


def get_connection():
    """Get a database connection from the pool."""
    # TODO: Implement actual connection pooling
    if _pool:
        return _pool.pop()
    return _create_connection()


def _create_connection():
    return {"url": DATABASE_URL, "active": True}


def release_connection(conn):
    """Return a connection to the pool."""
    _pool.append(conn)
PYEOF

cat > "$WORKSPACE/src/db/migrations.py" << 'PYEOF'
"""Schema migration utilities."""
import json
from pathlib import Path


MIGRATIONS_DIR = Path("migrations")


def list_migrations():
    """List all available migration files."""
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def get_current_version():
    # TODO: Read from _schema_version table
    return 0


def apply_migration(migration_path):
    """Apply a single migration file."""
    content = Path(migration_path).read_text()
    print(f"Applying: {migration_path}")
    return True


def migrate_up():
    """Run all pending migrations."""
    current = get_current_version()
    pending = [m for m in list_migrations() if _version_from(m) > current]
    # TODO: Wrap in transaction
    for m in pending:
        apply_migration(m)
    return len(pending)


def _version_from(path):
    return int(Path(path).stem.split("_")[0])
PYEOF

cat > "$WORKSPACE/src/db/queries.py" << 'PYEOF'
"""Common database queries."""


def find_users_by_role(conn, role):
    """Find all users with a given role."""
    return conn.execute(
        "SELECT * FROM users WHERE role = ?", (role,)
    ).fetchall()


def find_active_projects(conn):
    return conn.execute(
        "SELECT * FROM projects WHERE archived = 0"
    ).fetchall()


def count_tasks_by_status(conn, project_id):
    """Count tasks grouped by status for a project."""
    return conn.execute(
        "SELECT status, COUNT(*) FROM tasks WHERE project_id = ? GROUP BY status",
        (project_id,),
    ).fetchall()


def search_all(conn, query):
    results = {}
    for table in ["users", "projects", "tasks"]:
        results[table] = conn.execute(
            f"SELECT * FROM {table} WHERE name LIKE ?",
            (f"%{query}%",),
        ).fetchall()
    return results


def recent_activity(conn, limit=20):
    """Get recent activity log entries."""
    return conn.execute(
        "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
PYEOF

# --- Additional test files ---

cat > "$WORKSPACE/tests/test_handlers.py" << 'PYEOF'
"""Tests for request handlers."""


def test_handle_get_users():
    from src.handlers.user_handler import handle_get_users
    result = handle_get_users({})
    assert "users" in result
    assert "count" in result


def test_handle_login_missing_credentials():
    from src.handlers.auth_handler import handle_login
    result = handle_login({"body": {}})
    assert result[1] == 401


def test_handle_list_projects():
    from src.handlers.project_handler import handle_list_projects
    result = handle_list_projects({})
    assert "projects" in result


def test_handle_create_task():
    from src.handlers.task_handler import handle_create_task
    result = handle_create_task({"body": {"title": "Test"}}, project_id=1)
    assert result[1] == 201
PYEOF

cat > "$WORKSPACE/tests/test_services.py" << 'PYEOF'
"""Tests for service layer."""


def test_create_user():
    from src.services.user_service import create_user
    user = create_user("Test", "test@example.com")
    assert user.name == "Test"
    assert user.email == "test@example.com"


def test_cache_set_get():
    from src.services.cache_service import set, get, clear
    clear()
    set("key", "value")
    assert get("key") == "value"


def test_send_email():
    from src.services.email_service import send_email
    assert send_email("test@test.com", "Subject", "Body") is True
PYEOF

cat > "$WORKSPACE/tests/test_middleware.py" << 'PYEOF'
"""Tests for middleware."""


def test_authenticate_no_token():
    from src.middleware.auth import authenticate
    result = authenticate({})
    assert result is None


def test_cors_allowed_origin():
    from src.middleware.cors import cors_headers
    headers = cors_headers("http://localhost:3000")
    assert "Access-Control-Allow-Origin" in headers


def test_rate_limit_within_bounds():
    from src.middleware.rate_limit import check_rate_limit
    assert check_rate_limit("127.0.0.1") is True
PYEOF

# --- scripts/ directory ---
mkdir -p "$WORKSPACE/scripts"

cat > "$WORKSPACE/scripts/seed_db.py" << 'PYEOF'
"""Seed the database with sample data."""
from src.services.user_service import create_user
from src.services.project_service import create_project


def seed():
    """Create sample users and projects."""
    admin = create_user("Admin", "admin@example.com", role="admin")
    user1 = create_user("Alice", "alice@example.com")
    user2 = create_user("Bob", "bob@example.com")
    # TODO: Add sample tasks and activity log entries
    create_project("Alpha", admin, description="First project")
    create_project("Beta", user1)
    print("Database seeded.")


if __name__ == "__main__":
    seed()
PYEOF

cat > "$WORKSPACE/scripts/health_check.py" << 'PYEOF'
"""Health check script for monitoring."""
import sys
from src.db.connection import get_connection


def check():
    """Run health checks and exit with status code."""
    conn = get_connection()
    if not conn.get("active"):
        print("FAIL: database unreachable")
        sys.exit(1)
    print("OK: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    check()
PYEOF

# --- Additional config files ---

cat > "$WORKSPACE/config/api_keys.json" << 'EOF'
{
  "stripe_api_key": "sk_test_placeholder",
  "sendgrid_api_key": "SG.placeholder",
  "sentry_dsn": "https://placeholder@sentry.io/1",
  "redis_url": "redis://localhost:6379",
  "s3_bucket": "my-app-uploads",
  "cloudflare_token": "cf_placeholder"
}
EOF

cat > "$WORKSPACE/config/features.json" << 'EOF'
{
  "enable_signup": true,
  "enable_oauth": false,
  "enable_webhooks": true,
  "max_upload_size_mb": 50,
  "rate_limit_per_minute": 100,
  "maintenance_mode": false,
  "beta_users_only": false,
  "dark_mode": true
}
EOF

echo "$WORKSPACE"
