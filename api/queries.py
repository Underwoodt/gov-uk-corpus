"""Database queries for search functionality."""
import sys
from typing import List, Optional, Tuple

sys.path.insert(0, '/Users/tomunderwood/AI Brain/gov-uk-corpus')

from database.connection import get_connection
from api.models import SearchResult, CorpusStats

def search_keyword(
    query: str,
    organisations: Optional[List[str]] = None,
    document_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
) -> Tuple[List[SearchResult], int]:
    """
    Full-text keyword search with optional faceted filters.

    Uses PostgreSQL GIN index on full-text vector for fast searching.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Build WHERE clause dynamically
                where_clauses = [
                    "to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(body_text, '')) @@ plainto_tsquery('english', %s)"
                ]
                params = [query]

                if organisations:
                    placeholders = ",".join(["%s"] * len(organisations))
                    where_clauses.append(f"organisations && ARRAY[{placeholders}]")
                    params.extend(organisations)

                if document_type:
                    where_clauses.append("document_type = %s")
                    params.append(document_type)

                where_clause = " AND ".join(where_clauses)

                # Get total count
                count_query = f"""
                    SELECT COUNT(*) FROM gov_uk_urls
                    WHERE {where_clause}
                """
                cur.execute(count_query, params[:len(params)-len([p for p in params if p == document_type or p in (organisations or [])])])
                count_params = params.copy()
                if organisations:
                    count_params = [params[0]] + params[len([query]) + len(organisations):]
                if document_type:
                    count_params = [params[0]] + (params[1:-1] if organisations else []) + ([params[-1]] if params[-1] == document_type else [])

                # Simpler count
                cur.execute(f"SELECT COUNT(*) FROM gov_uk_urls WHERE {where_clause}", params)
                total = cur.fetchone()[0]

                # Get results with snippet
                search_query = f"""
                    SELECT id, url, title, description, document_type, organisations,
                           ts_headline('english', body_text, plainto_tsquery('english', %s),
                                       'StartSel=<strong>, StopSel=</strong>, MaxWords=20') as snippet
                    FROM gov_uk_urls
                    WHERE {where_clause}
                    ORDER BY ts_rank(to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(body_text, '')),
                                    plainto_tsquery('english', %s)) DESC,
                             updated_datetime DESC
                    LIMIT %s OFFSET %s
                """

                search_params = [query] + params + [query, limit, offset]
                cur.execute(search_query, search_params)

                results = []
                for row in cur.fetchall():
                    results.append(SearchResult(
                        id=row[0],
                        url=row[1],
                        title=row[2],
                        description=row[3],
                        document_type=row[4],
                        organisations=row[5],
                        snippet=row[6]
                    ))

                return results, total

    except Exception as e:
        print(f"Search error: {e}")
        raise

def search_advanced(
    query: str,
    title_filter: Optional[str] = None,
    document_type_filter: Optional[str] = None,
    organisations_filter: Optional[List[str]] = None,
    limit: int = 20,
    offset: int = 0
) -> Tuple[List[SearchResult], int]:
    """Advanced search with more granular filtering."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Build WHERE clause
                where_clauses = [
                    "to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(body_text, '')) @@ plainto_tsquery('english', %s)"
                ]
                params = [query]

                if title_filter:
                    where_clauses.append("title ILIKE %s")
                    params.append(f"%{title_filter}%")

                if document_type_filter:
                    where_clauses.append("document_type = %s")
                    params.append(document_type_filter)

                if organisations_filter:
                    placeholders = ",".join(["%s"] * len(organisations_filter))
                    where_clauses.append(f"organisations && ARRAY[{placeholders}]")
                    params.extend(organisations_filter)

                where_clause = " AND ".join(where_clauses)

                # Get total count
                cur.execute(f"SELECT COUNT(*) FROM gov_uk_urls WHERE {where_clause}", params)
                total = cur.fetchone()[0]

                # Get results
                search_query = f"""
                    SELECT id, url, title, description, document_type, organisations,
                           ts_headline('english', body_text, plainto_tsquery('english', %s),
                                       'StartSel=<strong>, StopSel=</strong>, MaxWords=20') as snippet
                    FROM gov_uk_urls
                    WHERE {where_clause}
                    ORDER BY ts_rank(to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(body_text, '')),
                                    plainto_tsquery('english', %s)) DESC
                    LIMIT %s OFFSET %s
                """

                search_params = [query] + params + [query, limit, offset]
                cur.execute(search_query, search_params)

                results = []
                for row in cur.fetchall():
                    results.append(SearchResult(
                        id=row[0],
                        url=row[1],
                        title=row[2],
                        description=row[3],
                        document_type=row[4],
                        organisations=row[5],
                        snippet=row[6]
                    ))

                return results, total

    except Exception as e:
        print(f"Advanced search error: {e}")
        raise

def get_stats() -> dict:
    """Get corpus statistics."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Total count
                cur.execute("SELECT COUNT(*) FROM gov_uk_urls")
                total = cur.fetchone()[0]

                # By status
                cur.execute("SELECT status, COUNT(*) FROM gov_uk_urls GROUP BY status")
                status_counts = {row[0]: row[1] for row in cur.fetchall()}

                # By document type
                cur.execute("SELECT document_type, COUNT(*) FROM gov_uk_urls WHERE document_type IS NOT NULL GROUP BY document_type ORDER BY COUNT(*) DESC LIMIT 20")
                doc_type_counts = {row[0]: row[1] for row in cur.fetchall()}

                # By organisation (need to unnest array)
                cur.execute("""
                    SELECT UNNEST(organisations) as org, COUNT(*) as cnt
                    FROM gov_uk_urls
                    WHERE organisations IS NOT NULL AND array_length(organisations, 1) > 0
                    GROUP BY org
                    ORDER BY cnt DESC
                    LIMIT 20
                """)
                org_counts = {row[0]: row[1] for row in cur.fetchall()}

                return {
                    "total_urls": total,
                    "urls_fetched": status_counts.get("fetched", 0),
                    "urls_pending": status_counts.get("discovered", 0),
                    "urls_failed": status_counts.get("failed", 0),
                    "document_types": doc_type_counts,
                    "organisations": org_counts,
                    "status_breakdown": status_counts
                }

    except Exception as e:
        print(f"Stats error: {e}")
        raise
