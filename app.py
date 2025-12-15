from fastapi import FastAPI, HTTPException, Request, status, Header
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text, event
from sqlalchemy.exc import SQLAlchemyError
import logging
import os
from typing import Any
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from urllib.parse import urlparse

# -------------------------------------------------
# LOGGING
# -------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# -------------------------------------------------
# ENV
# -------------------------------------------------
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
REX_API_KEY = os.getenv("REX_API_KEY")
RATE_LIMIT = os.getenv("RATE_LIMIT", "100/hour")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

if not REX_API_KEY:
    raise RuntimeError("REX_API_KEY is required")

# -------------------------------------------------
# DB PARSE
# -------------------------------------------------
parsed = urlparse(DATABASE_URL)
DB_HOST = parsed.hostname
DB_PORT = parsed.port
DB_NAME = parsed.path[1:]
DB_USER = parsed.username
DB_PASSWORD = parsed.password

# -------------------------------------------------
# APP
# -------------------------------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------
# RATE LIMIT
# -------------------------------------------------
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Rate limit exceeded"}
    )

# -------------------------------------------------
# READ-ONLY QUERY VALIDATOR
# -------------------------------------------------
def validate_read_only_query(sql: str):
    forbidden = [
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
        "TRUNCATE", "GRANT", "REVOKE", "CALL", "EXECUTE",
        "VACUUM", "REINDEX", "COPY", "IMPORT", "EXPORT"
    ]

    q = sql.upper().strip()

    if any(k in q for k in forbidden):
        raise HTTPException(403, "Write queries are forbidden")

    if not (q.startswith("SELECT") or q.startswith("WITH")):
        raise HTTPException(403, "Only SELECT/WITH queries allowed")

# -------------------------------------------------
# SQLALCHEMY ENGINE (READ-ONLY)
# -------------------------------------------------
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

@event.listens_for(engine, "connect")
def set_readonly(dbapi_conn, _):
    try:
        dbapi_conn.set_session(readonly=True, autocommit=False)
    except Exception as e:
        logger.warning(f"Readonly session failed: {e}")

# -------------------------------------------------
# SQLALCHEMY ENDPOINT
# -------------------------------------------------
@app.get("/sqlquery_alchemy/")
@limiter.limit(RATE_LIMIT)
async def sqlquery_alchemy(
    sqlquery: str,
    request: Request,
    rex_api_key: str = Header(None)  # <-- HEADER AUTH
) -> Any:

    if rex_api_key != REX_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    validate_read_only_query(sqlquery)

    try:
        with engine.connect() as conn:
            trans = conn.begin()
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")

            result = conn.execute(text(sqlquery))
            rows = result.fetchall()
            cols = result.keys()

            trans.commit()
            return [dict(zip(cols, row)) for row in rows]

    except SQLAlchemyError as e:
        logger.error(e)
        raise HTTPException(500, "Database error")

# -------------------------------------------------
# DIRECT PSYCOPG2 ENDPOINT
# -------------------------------------------------
@app.get("/sqlquery_direct/")
@limiter.limit(RATE_LIMIT)
async def sqlquery_direct(
    sqlquery: str,
    request: Request,
    rex_api_key: str = Header(None)  # <-- HEADER AUTH
) -> Any:

    if rex_api_key != REX_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    validate_read_only_query(sqlquery)

    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            cursor_factory=RealDictCursor
        )
        conn.set_session(readonly=True, autocommit=False)

        with conn.cursor() as cur:
            cur.execute(sqlquery)
            data = cur.fetchall()
            conn.commit()
            return list(data)

    except psycopg2.Error as e:
        logger.error(e)
        raise HTTPException(500, "Database error")

    finally:
        if conn:
            conn.close()

# -------------------------------------------------
# RUN
# -------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
