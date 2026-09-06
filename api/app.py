"""FastAPI search application for gov.uk corpus."""
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, '/Users/tomunderwood/AI Brain/gov-uk-corpus')

from database.connection import init_connection_pool, close_connection_pool
from api.routes import router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    # Startup
    init_connection_pool()
    print("Database connection pool initialized")
    yield
    # Shutdown
    close_connection_pool()
    print("Database connection pool closed")

# Create FastAPI app
app = FastAPI(
    title="Gov.uk Corpus Search API",
    description="Search API for gov.uk URLs and content",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(router)

@app.get("/")
async def root():
    """API health check."""
    return {
        "name": "Gov.uk Corpus Search API",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", 8000))
    uvicorn.run(app, host=host, port=port)
