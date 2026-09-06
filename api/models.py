"""Pydantic models for search API."""
from typing import List, Optional
from pydantic import BaseModel, Field

class SearchResult(BaseModel):
    """A single search result."""
    id: int = Field(..., description="URL record ID")
    url: str = Field(..., description="Full URL")
    title: Optional[str] = Field(None, description="Page title")
    description: Optional[str] = Field(None, description="Short description")
    document_type: Optional[str] = Field(None, description="Document type (e.g. policy, guidance)")
    organisations: Optional[List[str]] = Field(None, description="Associated organisations")
    snippet: Optional[str] = Field(None, description="Text snippet with search highlights")

class SearchResponse(BaseModel):
    """Search API response."""
    query: str = Field(..., description="The search query")
    total: int = Field(..., description="Total number of matching results")
    limit: int = Field(..., description="Results per page")
    offset: int = Field(..., description="Current result offset")
    results: List[SearchResult] = Field(..., description="List of search results")

    class Config:
        json_schema_extra = {
            "example": {
                "query": "climate change",
                "total": 245,
                "limit": 20,
                "offset": 0,
                "results": [
                    {
                        "id": 1,
                        "url": "https://www.gov.uk/climate-change-policy",
                        "title": "Climate Change Policy",
                        "description": "UK government policy on climate change",
                        "document_type": "policy",
                        "organisations": ["DEFRA"],
                        "snippet": "...climate change policy framework..."
                    }
                ]
            }
        }

class CorpusStats(BaseModel):
    """Corpus statistics."""
    total_urls: int = Field(..., description="Total URLs in corpus")
    urls_fetched: int = Field(..., description="URLs with metadata fetched")
    urls_pending: int = Field(..., description="URLs pending metadata fetch")
    document_types: dict = Field(..., description="Count by document type")
    organisations: dict = Field(..., description="Count by organisation")
