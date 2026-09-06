"""Search API routes."""
import sys
from typing import Optional, List
from fastapi import APIRouter, Query, HTTPException

sys.path.insert(0, '/Users/tomunderwood/AI Brain/gov-uk-corpus')

from api.queries import search_keyword, search_advanced
from api.models import SearchResponse, SearchResult

router = APIRouter(prefix="/search", tags=["search"])

@router.get("", response_model=SearchResponse)
async def keyword_search(
    q: str = Query(..., min_length=3, max_length=200, description="Search query"),
    organisations: Optional[List[str]] = Query(None, description="Filter by organisations"),
    document_type: Optional[str] = Query(None, description="Filter by document type"),
    limit: int = Query(20, ge=1, le=100, description="Number of results"),
    offset: int = Query(0, ge=0, description="Result offset for pagination")
):
    """
    Keyword search across gov.uk URLs and content.

    Searches in: title, description, body_text using full-text search.

    **Query Parameters:**
    - `q`: Search query (required)
    - `organisations`: Filter by organisation (optional, can be multiple)
    - `document_type`: Filter by document type (optional)
    - `limit`: Results per page (default: 20, max: 100)
    - `offset`: Pagination offset (default: 0)

    **Example:**
    ```
    GET /search?q=climate+change&organisations=DEFRA&limit=10
    ```
    """
    try:
        results, total = search_keyword(
            query=q,
            organisations=organisations,
            document_type=document_type,
            limit=limit,
            offset=offset
        )
        return SearchResponse(
            query=q,
            total=total,
            limit=limit,
            offset=offset,
            results=results
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.post("", response_model=SearchResponse)
async def advanced_search(
    q: str = Query(..., min_length=3, max_length=200, description="Search query"),
    title: Optional[str] = Query(None, description="Filter by title (substring match)"),
    document_type: Optional[str] = Query(None, description="Filter by document type"),
    organisations: Optional[List[str]] = Query(None, description="Filter by organisations"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """
    Advanced search with faceted filters.

    Combines keyword search with filtering by:
    - document_type: Exact match on document type
    - organisations: Match any organisation in the list
    - title: Substring match in title field

    **Example:**
    ```
    GET /search?q=climate&document_type=policy&organisations=DEFRA
    ```
    """
    try:
        results, total = search_advanced(
            query=q,
            title_filter=title,
            document_type_filter=document_type,
            organisations_filter=organisations,
            limit=limit,
            offset=offset
        )
        return SearchResponse(
            query=q,
            total=total,
            limit=limit,
            offset=offset,
            results=results
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.get("/stats")
async def search_stats():
    """Get corpus statistics."""
    try:
        from api.queries import get_stats
        stats = get_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")
