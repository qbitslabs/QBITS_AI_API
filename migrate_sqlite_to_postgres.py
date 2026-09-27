#!/usr/bin/env python3
# One-time ETL from leftover AI SQLite into dedicated Postgres.
# Do not point this at the CGS clinic database.
"""
CGS AI Service — SQLite to Supabase PostgreSQL Migration ETL
Reads all records from ai_service.db and inserts them into the "ai_service" schema.
Idempotent: ON CONFLICT DO UPDATE for mutable tables, DO NOTHING for append-only rows.
"""

import os
import sys
import json
import sqlite3
import asyncio
from datetime import datetime
from pathlib import Path

try:
    import asyncpg
except ImportError:
    print("[ERROR] asyncpg is required. Run: pip install asyncpg")
    sys.exit(1)

def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()

SQLITE_DB_PATH = os.path.join(os.path.dirname(__file__), "ai_service.db")

SUPABASE_URL = os.getenv("SUPABASE_DIRECT_URL") or os.getenv("DATABASE_URL", "")


def parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00").split("+")[0])
    except Exception:
        try:
            return datetime.strptime(str(val)[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None


def parse_json(val):
    if not val:
        return {}
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return {}


def parse_bool(val):
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "t", "yes")


async def run_migration():
    if not os.path.exists(SQLITE_DB_PATH):
        print(f"[ERROR] SQLite database not found at {SQLITE_DB_PATH}")
        sys.exit(1)

    if not SUPABASE_URL or "[YOUR-PASSWORD]" in SUPABASE_URL or "sqlite" in SUPABASE_URL:
        print("[ERROR] Set DATABASE_URL in backend/ai_service/.env to the dedicated AI Postgres URL.")
        sys.exit(1)

    clean_url = (
        SUPABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres://", "postgresql://")
    )

    print("==================================================================")
    print(" CGS AI SERVICE: SQLITE -> SUPABASE POSTGRESQL MIGRATION ETL")
    print("==================================================================")
    print(f"[*] Source Database: {SQLITE_DB_PATH}")
    print("[*] Target Schema:   ai_service")

    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    sq_cursor = sqlite_conn.cursor()

    print("[*] Connecting to PostgreSQL...")
    try:
        pg_conn = await asyncpg.connect(clean_url, ssl="require")
        print("[+] Connected successfully.")
    except Exception as e:
        print(f"[-] Failed to connect to PostgreSQL: {e}")
        sys.exit(1)

    try:
        print("[*] Creating dedicated ai_service schema and tables on THIS database only...")
        await pg_conn.execute("CREATE SCHEMA IF NOT EXISTS ai_service;")
        await pg_conn.execute("SET search_path TO ai_service, public;")
        ddl_path = Path(__file__).parent / "sql" / "ai_service_schema.sql"
        if ddl_path.exists():
            for raw_stmt in ddl_path.read_text(encoding="utf-8").split(";"):
                lines = [
                    line
                    for line in raw_stmt.splitlines()
                    if line.strip() and not line.strip().startswith("--")
                ]
                stmt = "\n".join(lines).strip()
                if not stmt:
                    continue
                try:
                    await pg_conn.execute(stmt)
                except Exception as ddl_err:
                    print(f"    [warn] DDL skipped/failed: {ddl_err}")

        entities = sq_cursor.execute("SELECT * FROM entities").fetchall()
        print(f"[*] Migrating 'entities' ({len(entities)} rows)...")
        for row in entities:
            r = dict(row)
            await pg_conn.execute(
                """
                INSERT INTO ai_service.entities (
                    id, type, name, external_id, system_prompt, configuration, status, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (id) DO UPDATE SET
                    type = EXCLUDED.type,
                    name = EXCLUDED.name,
                    external_id = EXCLUDED.external_id,
                    system_prompt = EXCLUDED.system_prompt,
                    configuration = EXCLUDED.configuration,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at;
                """,
                r["id"], r["type"], r["name"], r.get("external_id"), r.get("system_prompt"),
                json.dumps(parse_json(r.get("configuration"))), r.get("status", "ACTIVE"),
                parse_dt(r["created_at"]), parse_dt(r["updated_at"]),
            )
        print("    [OK] Entities migrated.")

        conversations = sq_cursor.execute("SELECT * FROM conversations").fetchall()
        print(f"[*] Migrating 'conversations' ({len(conversations)} rows)...")
        for row in conversations:
            r = dict(row)
            await pg_conn.execute(
                """
                INSERT INTO ai_service.conversations (
                    id, entity_id, participant_id, channel, state, last_appointment_id,
                    last_appointment_date, cgs_conversation_id, awaiting_rebook, rebook_asked_date,
                    last_activity, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (id) DO UPDATE SET
                    state = EXCLUDED.state,
                    last_activity = EXCLUDED.last_activity,
                    updated_at = EXCLUDED.updated_at,
                    awaiting_rebook = EXCLUDED.awaiting_rebook,
                    cgs_conversation_id = EXCLUDED.cgs_conversation_id;
                """,
                r["id"], r["entity_id"], r["participant_id"], r.get("channel", "WHATSAPP"),
                r.get("state", "AI_ACTIVE"), r.get("last_appointment_id"), r.get("last_appointment_date"),
                r.get("cgs_conversation_id"), parse_bool(r.get("awaiting_rebook")), r.get("rebook_asked_date"),
                parse_dt(r.get("last_activity")), parse_dt(r["created_at"]), parse_dt(r["updated_at"]),
            )
        print("    [OK] Conversations migrated.")

        messages = sq_cursor.execute("SELECT * FROM messages").fetchall()
        print(f"[*] Migrating 'messages' ({len(messages)} rows)...")
        for row in messages:
            r = dict(row)
            await pg_conn.execute(
                """
                INSERT INTO ai_service.messages (
                    id, conversation_id, sender_type, content, tokens, msg_metadata, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (id) DO NOTHING;
                """,
                r["id"], r["conversation_id"], r["sender_type"], r["content"],
                r.get("tokens", 0), json.dumps(parse_json(r.get("msg_metadata"))),
                parse_dt(r["created_at"]),
            )
        print("    [OK] Messages migrated.")

        summaries = sq_cursor.execute("SELECT * FROM conversation_summaries").fetchall()
        print(f"[*] Migrating 'conversation_summaries' ({len(summaries)} rows)...")
        for row in summaries:
            r = dict(row)
            await pg_conn.execute(
                """
                INSERT INTO ai_service.conversation_summaries (
                    id, conversation_id, version, summary_text, summary_json, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (id) DO UPDATE SET
                    version = EXCLUDED.version,
                    summary_text = EXCLUDED.summary_text,
                    summary_json = EXCLUDED.summary_json,
                    updated_at = EXCLUDED.updated_at;
                """,
                r["id"], r["conversation_id"], r.get("version", 1), r["summary_text"],
                json.dumps(parse_json(r.get("summary_json"))),
                parse_dt(r["created_at"]), parse_dt(r["updated_at"]),
            )
        print("    [OK] Conversation summaries migrated.")

        addons = sq_cursor.execute("SELECT * FROM summary_addons").fetchall()
        print(f"[*] Migrating 'summary_addons' ({len(addons)} rows)...")
        for row in addons:
            r = dict(row)
            await pg_conn.execute(
                """
                INSERT INTO ai_service.summary_addons (
                    id, conversation_id, type, content, status, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status;
                """,
                r["id"], r["conversation_id"], r["type"], r["content"],
                r.get("status", "PENDING"), parse_dt(r["created_at"]),
            )
        print("    [OK] Summary addons migrated.")

        logs = sq_cursor.execute("SELECT * FROM ai_usage_logs").fetchall()
        print(f"[*] Migrating 'ai_usage_logs' ({len(logs)} rows)...")
        for row in logs:
            r = dict(row)
            await pg_conn.execute(
                """
                INSERT INTO ai_service.ai_usage_logs (
                    id, entity_id, conversation_id, request_id, model, prompt_tokens,
                    completion_tokens, total_tokens, estimated_cost, duration_ms, success,
                    error_message, usage_metadata, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                ON CONFLICT (id) DO NOTHING;
                """,
                r["id"], r.get("entity_id"), r.get("conversation_id"), r["request_id"],
                r["model"], r.get("prompt_tokens", 0), r.get("completion_tokens", 0),
                r.get("total_tokens", 0), float(r.get("estimated_cost") or 0.0),
                r.get("duration_ms", 0), parse_bool(r.get("success", 1)),
                r.get("error_message"), json.dumps(parse_json(r.get("usage_metadata"))),
                parse_dt(r["created_at"]),
            )
        print("    [OK] AI usage logs migrated.")

        print("\n==================================================================")
        print(" MIGRATION VERIFICATION AUDIT")
        print("==================================================================")
        tables = [
            "entities",
            "conversations",
            "messages",
            "conversation_summaries",
            "summary_addons",
            "ai_usage_logs",
        ]
        all_match = True
        for t in tables:
            sq_count = sq_cursor.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            pg_count = await pg_conn.fetchval(f"SELECT COUNT(*) FROM ai_service.{t}")
            status = "MATCH" if sq_count == pg_count else "MISMATCH"
            if status != "MATCH":
                all_match = False
            print(f" * ai_service.{t:<22} -> SQLite: {sq_count:<5} | Postgres: {pg_count:<5} [{status}]")

        print("==================================================================")
        if all_match:
            print("[SUCCESS] All tables migrated with 100% data parity!")
        else:
            print("[WARNING] Row count mismatch detected. Please review output above.")
        print("==================================================================")

    finally:
        await pg_conn.close()
        sqlite_conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
