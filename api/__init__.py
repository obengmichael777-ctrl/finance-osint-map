"""
API Application Entry Point
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes.markers import app as markers_app

# Main application
app = FastAPI(
    title="Supermarket OSINT API",
    description="Pan-Asian Retail Analytics Platform",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Mount marker routes
app.mount("/api/v1", markers_app)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "Supermarket OSINT API",
        "version": "1.0.0"
    }


# Run with: uvicorn api:app --reload --port 8000
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
