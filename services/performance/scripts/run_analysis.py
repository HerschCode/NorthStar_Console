"""Convenience entry point to run the API locally: `python scripts/run_analysis.py`."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=True)
