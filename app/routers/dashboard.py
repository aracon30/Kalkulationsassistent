"""Dashboard mit Kennzahlen."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Calculation, Document, ImportJob, ImportStatus, Offer, OfferItem, ReviewIssue
from app.web import templates

router = APIRouter()


def collect_kpis(db: Session) -> dict:
    offers = db.scalar(select(func.count(Offer.id))) or 0
    items = db.scalar(select(func.count(OfferItem.id))) or 0
    by_year = db.execute(select(Offer.year, func.count(Offer.id)).group_by(Offer.year).order_by(Offer.year)).all()
    by_type = db.execute(select(OfferItem.product_type, func.count(OfferItem.id)).group_by(OfferItem.product_type)
                         .order_by(func.count(OfferItem.id).desc())).all()
    doc_counts = dict(db.execute(select(Document.import_status, func.count(Document.id)).where(Document.is_current.is_(True))
                                 .group_by(Document.import_status)).all())
    imported, failed = doc_counts.get(ImportStatus.IMPORTIERT, 0), doc_counts.get(ImportStatus.FEHLER, 0)
    open_reviews = db.scalar(select(func.count(ReviewIssue.id)).where(ReviewIssue.status == "offen")) or 0
    open_errors = db.scalar(select(func.count(ReviewIssue.id)).where(ReviewIssue.status == "offen", ReviewIssue.severity == "fehler")) or 0
    current = select(Calculation).where(Calculation.is_current.is_(True)).subquery()
    avg_price = db.execute(select(OfferItem.product_type, func.avg(current.c.offer_price), func.avg(current.c.manufacturing_hours), func.count(current.c.id))
                           .join(current, current.c.offer_item_id == OfferItem.id).group_by(OfferItem.product_type)).all()
    overall = db.execute(select(func.avg(current.c.offer_price), func.avg(current.c.manufacturing_hours))).one()
    last_job = db.scalars(select(ImportJob).order_by(ImportJob.id.desc())).first()
    return {
        "offers": offers, "item_count": items, "by_year": by_year, "by_type": by_type,
        "docs_imported": imported, "docs_failed": failed, "docs_new": doc_counts.get(ImportStatus.NEU, 0),
        "success_rate": (imported / (imported + failed)) if (imported + failed) else None,
        "open_reviews": open_reviews, "open_errors": open_errors,
        "avg_by_type": avg_price, "avg_price": overall[0], "avg_hours": overall[1], "last_job": last_job,
        "max_year": max((c for _, c in by_year), default=0), "max_type": max((c for _, c in by_type), default=0),
    }


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "dashboard.html", {"k": collect_kpis(db), "nav": "dashboard"})
