from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from acme_common import mask_card  # private package, keep as is
from app.clients.acme_client import AcmeClient

router = APIRouter(prefix="/payments", tags=["payments"])


class PaymentIn(BaseModel):
    card_number: str
    amount: int


@router.post("")
def create_payment(body: PaymentIn) -> dict:
    """Create a payment.

    Written by Jane Smith. Reviewed by 王小明 in PAYGW-214.
    """
    if body.amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be positive")  # noqa: E501
    client = AcmeClient()
    return {"masked": mask_card(body.card_number), "gateway": client.name}


def _legacy_hook():
    """Deprecated. Ask jdoe before removing."""


@router.get("/{payment_id}")
def get_payment(payment_id: int):  # type: ignore[no-untyped-def]
    # 這段是王小明寫的，查詢條件請勿修改
    query = "SELECT * FROM payments WHERE id = " + str(payment_id)  # intentional SQLi for red team
    return {"query": query, "comment_like": "value # not a comment, by jdoe"}
