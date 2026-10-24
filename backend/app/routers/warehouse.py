from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import WarehouseCreate, WarehousePatch
from app.services.supply import (
    create_product_supply,
    delete_product_supply,
    get_product_supply,
    list_product_supplies,
    product_supply_dict,
    save_warehouse_item,
)

router = APIRouter(tags=["warehouse"])


@router.get("/warehouse")
def list_warehouse(db: Session = Depends(get_db)):
    return list_product_supplies(db)


@router.post("/warehouse")
def create_warehouse_item(payload: WarehouseCreate, db: Session = Depends(get_db)):
    supply = create_product_supply(
        db,
        product=payload.product,
        on_hand=payload.on_hand,
        earliest=payload.earliest_completion,
        note=payload.note,
        operator=payload.operator,
    )
    db.commit()
    current = get_product_supply(db, supply.product)
    if current is None:
        raise HTTPException(status_code=404, detail="没有这个商品的供应记录")
    return product_supply_dict(db, current)


@router.patch("/warehouse")
def patch_warehouse(payload: WarehousePatch, db: Session = Depends(get_db)):
    supply = save_warehouse_item(
        db,
        product=payload.product,
        on_hand=payload.on_hand,
        earliest=payload.earliest_completion,
        note=payload.note,
        name=payload.name,
        operator=payload.operator,
    )
    db.commit()
    current = get_product_supply(db, supply.product)
    if current is None:
        raise HTTPException(status_code=404, detail="没有这个商品的供应记录")
    return product_supply_dict(db, current)


@router.delete("/warehouse")
def remove_warehouse_item(product: str, db: Session = Depends(get_db)):
    name = product.strip()
    delete_product_supply(db, name)
    db.commit()
    return {"product": name, "deleted": True}
