import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db import Base, ensure_schema, get_engine, open_session
from app.routers import assistant, customers, meta, orders, reviews, sources, tasks, warehouse
from app.seed import ensure_catalog_supplies, seed_if_empty
from app.services.supply import backfill_product_supplies


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    ensure_schema()
    db = open_session()
    try:
        seed_if_empty(db)
        ensure_catalog_supplies(db)
        backfill_product_supplies(db)
        db.commit()
    finally:
        db.close()


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="单迹", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(meta.router)
    app.include_router(assistant.router)
    app.include_router(customers.router)
    app.include_router(orders.router)
    app.include_router(sources.router)
    app.include_router(reviews.router)
    app.include_router(tasks.router)
    app.include_router(warehouse.router)

    static_root = os.getenv("STATIC_DIR", "").strip()
    if static_root:
        path = Path(static_root)
        if path.is_dir():
            app.mount("/", StaticFiles(directory=str(path), html=True), name="spa")

    return app


app = create_app()
