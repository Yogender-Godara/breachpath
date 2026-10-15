"""BreachPath API. Run: uvicorn api.main:app. Routes are under /v1, except /healthz and /readyz."""
from fastapi import FastAPI

from api import errors
from api.routes import analyze, health, incidents, scenarios

app = FastAPI(title="BreachPath", version="0.1.0")
errors.install(app)
app.include_router(health.router)
for module in (scenarios, analyze, incidents):
    app.include_router(module.router)
