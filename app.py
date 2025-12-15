from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text, event
from sqlalchemy.exc import SQLAlchemyError
import logging
import os
from typing import Any, Union
from starlette.middleware.base import BaseHTTPMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from urllib.parse import urlparse # FIX: Ensure urlparse is imported before its usage

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Database URL and credentials
DATABASE_URL = os.getenv("DATABASE_URL")
REX_API_KEY = os.getenv("REX_API_KEY")

if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable is not set")
    raise ValueError("DATABASE_URL environment variable is required")

if not REX_API_KEY:
    logger.error("REX_API_KEY environment variable is not set")
    raise ValueError("REX_API_KEY environment variable is required")

# Parse connection details from DATABASE_URL
parsed_url = urlparse(DATABASE_URL)
DB_HOST = parsed_url.hostname
DB_PORT = parsed_url.port
DB_NAME = parsed_url.path[1:]   # Remove leading slash
DB_USER = parsed_url.username
DB_PASSWORD = parsed_url.password

# Initialize FastAPI
app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # Update with your frontend origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting configuration (default 100/hour)
RATE_LIMIT = os.getenv("RATE_LIMIT", "100/hour")
logger.info(f"Using rate limit: {RATE_LIMIT}")

# Initialize SlowAPI limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

# Custom 429 handler (match prior app behavior)
async def custom_rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Rate limit exceeded. Please try again later or contact your administrator."}
    )

app.add_exception_handler(RateLimitExceeded, custom_rate_limit_exceeded_handler)


# --- FIX 1: SECURITY: READ-ONLY QUERY VALIDATOR ---
def validate_read_only_query(sqlquery: str):
    """Checks if the query contains any forbidden write/DDL keywords and ensures it is a SELECT/WITH query."""
    forbidden_keywords = [
        'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 
        'TRUNCATE', 'GRANT', 'REVOKE', 'CALL', 'EXECUTE', 'SET SESSION AUTHORIZATION',
        'VACUUM', 'REINDEX', 'COPY', 'IMPORT', 'EXPORT' 
    ]
    
    query_upper = sqlquery.upper().strip()
    
    # 1. Check for common forbidden keywords
    if any(keyword in query_upper for keyword in forbidden_keywords):
        raise HTTPException(
            status_code=403, 
            detail="Write operations (INSERT, UPDATE, DELETE, etc.) are forbidden on this API endpoint."
        )
    
    # 2. Stronger check: Ensure it starts with a read operation (SELECT or WITH)
    if not (query_upper.startswith('SELECT') or query_upper.startswith('WITH')):
        raise HTTPException(
            status_code=403, 
            detail="Only queries starting with SELECT or WITH (for CTEs) are permitted."
        )
# --- END VALIDATOR ---


# Create SQLAlchemy engine
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# Ensure all SQLAlchemy connections are session-level read-only
@event.listens_for(engine, "connect")
def set_session_readonly(dbapi_connection, connection_record):
    try:
        # dbapi_connection is the raw psycopg2 connection
        dbapi_connection.set_session(readonly=True, autocommit=False)
        logger.debug("SQLAlchemy DBAPI session set to readonly")
    except Exception as e:
        logger.warning(f"Failed to set SQLAlchemy session to readonly: {e}")

@app.get("/sqlquery_alchemy/")
@limiter.limit(RATE_LIMIT)
async def sqlquery_alchemy(sqlquery: str, api_key: str, request: Request) -> Any:
    """Execute SQL query using SQLAlchemy and return results directly."""
    if api_key != REX_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
        
    # FIX 2A: Apply application-level validation
    validate_read_only_query(sqlquery)

    logger.debug(f"Received API call to SQLAlchemy endpoint: {request.url}")
    logger.debug(f"SQL Query: {sqlquery}")

    try:
        with engine.connect() as connection:
            # Start a read-only transaction to enforce read-only at the DB level
            trans = connection.begin()
            try:
                # This line explicitly sets the transaction to read only, 
                # which is a strong defense against write queries.
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")

                # Execute query
                result = connection.execute(text(sqlquery))
                
                # If SELECT query, return results
                if sqlquery.strip().lower().startswith('select'):
                    # Get column names
                    columns = result.keys()
                    
                    # Fetch all rows
                    rows = result.fetchall()
                    
                    # Convert rows to list of dictionaries
                    results = [dict(zip(columns, row)) for row in rows]
                    
                    logger.debug(f"Query executed successfully via SQLAlchemy, returned {len(results)} rows")
                    trans.commit()
                    return results
                
                # For non-SELECT queries, the attempt will fail due to read-only transaction/validator
                # We raise an explicit exception here just in case the validator failed, 
                # though the 'SET TRANSACTION READ ONLY' should have already caused a database error.
                else:
                    trans.rollback()
                    logger.warning("Non-SELECT query attempted in read-only transaction")
                    raise HTTPException(status_code=403, detail="Non-SELECT queries are forbidden on this read-only API endpoint.")
            except:
                trans.rollback()
                raise

    except SQLAlchemyError as e:
        logger.error(f"SQLAlchemy error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in SQLAlchemy endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@app.get("/sqlquery_direct/")
@limiter.limit(RATE_LIMIT)
async def sqlquery_direct(sqlquery: str, api_key: str, request: Request) -> Any:
    """Execute SQL query using direct psycopg2 connection and return results."""
    if api_key != REX_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # FIX 2B: Apply application-level validation
    validate_read_only_query(sqlquery)

    logger.debug(f"Received API call to direct connection endpoint: {request.url}")
    logger.debug(f"SQL Query: {sqlquery}")

    connection = None
    try:
        # Create direct connection
        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            cursor_factory=RealDictCursor    # This will return results as dictionaries
        )
        # Enforce read-only at the session level for this connection
        connection.set_session(readonly=True, autocommit=False)
        
        with connection.cursor() as cursor:
            # Execute query
            cursor.execute(sqlquery)
            
            # If SELECT query, return results
            if sqlquery.strip().lower().startswith('select'):
                results = cursor.fetchall()
                # Commit here to finalize the read transaction cleanly
                connection.commit() 
                logger.debug(f"Query executed successfully via direct connection, returned {len(results)} rows")
                # RealDictCursor returns results as dictionaries, so we can return directly
                return list(results)
            
            # FIX 3: For non-SELECT queries, explicitly block them
            else:
                connection.rollback() # Ensure no partial write attempts are committed
                logger.warning(f"Attempted non-SELECT query blocked in direct connection: {sqlquery}")
                raise HTTPException(
                    status_code=403, 
                    detail="Non-SELECT queries are forbidden on this read-only API endpoint."
                )

    except psycopg2.Error as e:
        logger.error(f"PostgreSQL error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in direct connection endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
    finally:
        if connection:
            connection.close()
            logger.debug("Database connection closed")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)