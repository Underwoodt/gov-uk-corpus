"""Database connection and query execution utilities."""
import os
import logging
from contextlib import contextmanager
from psycopg2 import pool, Error, sql
from datetime import datetime

logger = logging.getLogger(__name__)

# Connection pool - lazy initialized
_connection_pool = None

def init_connection_pool(
    host: str = os.getenv("DB_HOST", "localhost"),
    port: int = int(os.getenv("DB_PORT", 5432)),
    database: str = os.getenv("DB_NAME", "gov_uk_urls"),
    user: str = os.getenv("DB_USER", "gov_uk_crawler"),
    password: str = os.getenv("DB_PASSWORD", ""),
    minconn: int = 2,
    maxconn: int = 10
):
    """Initialize the connection pool."""
    global _connection_pool
    if _connection_pool is None:
        try:
            _connection_pool = pool.SimpleConnectionPool(
                minconn,
                maxconn,
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                connect_timeout=10
            )
            logger.info(f"Connection pool initialized: {minconn}-{maxconn} connections")
        except Error as e:
            logger.error(f"Failed to initialize connection pool: {e}")
            raise

@contextmanager
def get_connection():
    """Get a connection from the pool."""
    if _connection_pool is None:
        init_connection_pool()

    conn = _connection_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        _connection_pool.putconn(conn)

@contextmanager
def get_cursor(conn=None):
    """Get a cursor, optionally from a provided connection."""
    if conn is None:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()
    else:
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            cursor.close()

def execute_query(query: str, params: tuple = None, fetch_one: bool = False):
    """Execute a SELECT query and return results."""
    try:
        with get_connection() as conn:
            with get_cursor(conn) as cur:
                cur.execute(query, params or ())
                if fetch_one:
                    return cur.fetchone()
                return cur.fetchall()
    except Error as e:
        logger.error(f"Query execution error: {e}")
        raise

def execute_update(query: str, params: tuple = None):
    """Execute INSERT, UPDATE, or DELETE and return rows affected."""
    try:
        with get_connection() as conn:
            with get_cursor(conn) as cur:
                cur.execute(query, params or ())
                return cur.rowcount
    except Error as e:
        logger.error(f"Update execution error: {e}")
        raise

def batch_insert_urls(urls: list, sitemap_filename: str, run_datetime: datetime):
    """Batch insert URLs into gov_uk_urls table."""
    try:
        with get_connection() as conn:
            with get_cursor(conn) as cur:
                for url in urls:
                    cur.execute(
                        """
                        INSERT INTO gov_uk_urls (url, sitemap_filename, run_datetime, status)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (url) DO NOTHING
                        """,
                        (url, sitemap_filename, run_datetime, 'discovered')
                    )
                rows_inserted = cur.rowcount
                logger.info(f"Inserted {rows_inserted} URLs from {sitemap_filename}")
                return rows_inserted
    except Error as e:
        logger.error(f"Batch insert error: {e}")
        raise

def update_url_metadata(url_id: int, data: dict):
    """Update URL record with fetched metadata."""
    try:
        with get_connection() as conn:
            with get_cursor(conn) as cur:
                # Build dynamic UPDATE query
                set_clause = ", ".join([f"{k} = %s" for k in data.keys()])
                query = f"UPDATE gov_uk_urls SET {set_clause}, updated_datetime = NOW() WHERE id = %s"
                values = list(data.values()) + [url_id]

                cur.execute(query, values)
                logger.debug(f"Updated URL {url_id}: {list(data.keys())}")
    except Error as e:
        logger.error(f"URL update error: {e}")
        raise

def log_error(url_id: int, error_msg: str):
    """Log error for a URL and increment attempt count."""
    try:
        with get_connection() as conn:
            with get_cursor(conn) as cur:
                cur.execute(
                    """
                    UPDATE gov_uk_urls
                    SET status = %s, last_error = %s, attempt_count = attempt_count + 1,
                        updated_datetime = NOW()
                    WHERE id = %s
                    """,
                    ('failed', error_msg, url_id)
                )
    except Error as e:
        logger.error(f"Error logging failed URL: {e}")
        raise

def start_crawl_run(stage: str):
    """Start a new crawl run and return the run ID."""
    try:
        with get_connection() as conn:
            with get_cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO crawl_runs (run_datetime, stage, status)
                    VALUES (NOW(), %s, %s)
                    RETURNING id
                    """,
                    (stage, 'in_progress')
                )
                run_id = cur.fetchone()[0]
                logger.info(f"Started crawl run {run_id} for stage: {stage}")
                return run_id
    except Error as e:
        logger.error(f"Error starting crawl run: {e}")
        raise

def update_crawl_run(run_id: int, **kwargs):
    """Update crawl run progress."""
    try:
        with get_connection() as conn:
            with get_cursor(conn) as cur:
                set_clause = ", ".join([f"{k} = %s" for k in kwargs.keys()])
                query = f"UPDATE crawl_runs SET {set_clause} WHERE id = %s"
                values = list(kwargs.values()) + [run_id]

                cur.execute(query, values)
    except Error as e:
        logger.error(f"Error updating crawl run: {e}")
        raise

def get_discovered_urls(limit: int = 1000):
    """Get URLs with status='discovered' that need enrichment."""
    try:
        with get_connection() as conn:
            with get_cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT id, url FROM gov_uk_urls
                    WHERE status = %s
                    ORDER BY id ASC
                    LIMIT %s
                    """,
                    ('discovered', limit)
                )
                return cur.fetchall()
    except Error as e:
        logger.error(f"Error fetching discovered URLs: {e}")
        raise

def get_url_count(status: str = None):
    """Get count of URLs by status."""
    try:
        with get_connection() as conn:
            with get_cursor(conn) as cur:
                if status:
                    cur.execute(
                        "SELECT COUNT(*) FROM gov_uk_urls WHERE status = %s",
                        (status,)
                    )
                else:
                    cur.execute("SELECT COUNT(*) FROM gov_uk_urls")
                return cur.fetchone()[0]
    except Error as e:
        logger.error(f"Error counting URLs: {e}")
        raise

def close_connection_pool():
    """Close all connections in the pool."""
    global _connection_pool
    if _connection_pool:
        _connection_pool.closeall()
        _connection_pool = None
        logger.info("Connection pool closed")
