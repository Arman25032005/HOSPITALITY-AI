import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.routes.chat import get_llm_dep
from app.api.routes.knowledge import get_embedder_dep, get_store_dep
from app.channels import telegram, twilio
from app.channels.base import ChannelError
from app.channels.delivery import SenderFactory
from app.channels.inbox import process_inbound
from app.channels.registry import build_sender
from app.config import Settings, get_settings
from app.db.session import get_db
from app.llm.base import LLMProvider
from app.models import Hotel, IntegrationConfig
from app.rag.embeddings import EmbeddingProvider
from app.rag.vectorstore import VectorStore

log = logging.getLogger("hospitality.webhooks")
router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
MAX_WEBHOOK_BYTES = 64 * 1024


def get_senders(settings: Settings = Depends(get_settings)) -> SenderFactory:
    return lambda channel: build_sender(settings, channel)


def _check_size(request: Request) -> None:
    try:
        size = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise HTTPException(400, "Invalid Content-Length") from None
    if size > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "Payload too large")


def _integration(db: Session, provider: str, *, number: str | None = None,
                 integration_id: uuid.UUID | None = None) -> IntegrationConfig | None:
    q = select(IntegrationConfig).where(IntegrationConfig.provider == provider,
                                        IntegrationConfig.enabled.is_(True))
    if integration_id:
        q = q.where(IntegrationConfig.id == integration_id)
    if number:
        q = q.where(IntegrationConfig.config["number"].astext == number)
    return db.scalars(q).first()


@router.post("/twilio/whatsapp")
async def twilio_whatsapp(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    senders: SenderFactory = Depends(get_senders), llm: LLMProvider | None = Depends(get_llm_dep),
    embedder: EmbeddingProvider = Depends(get_embedder_dep),
    store: VectorStore = Depends(get_store_dep),
):
    _check_size(request)
    form = await request.form()
    params = {k: [str(v) for v in form.getlist(k)] for k in form}
    if settings.twilio_webhook_validate:
        if not settings.twilio_auth_token or not settings.public_base_url:
            raise HTTPException(503, "WhatsApp webhook is not configured")
        url = settings.public_base_url.rstrip("/") + request.url.path
        if not twilio.verify_signature(settings.twilio_auth_token, url, params,
                                       request.headers.get("x-twilio-signature")):
            raise HTTPException(403, "Invalid signature")
    try:
        inbound = twilio.parse_inbound({k: v[0] for k, v in params.items() if v})
    except ChannelError as exc:
        raise HTTPException(400, str(exc)) from None
    integ = _integration(db, twilio.PROVIDER, number=inbound.recipient)
    if integ is None:
        raise HTTPException(404, "No hotel is configured for this number")
    hotel = db.get(Hotel, integ.hotel_id)
    await run_in_threadpool(process_inbound, db, settings=settings, hotel=hotel, inbound=inbound,
                            senders=senders, llm=llm, embedder=embedder, store=store)
    return Response("<Response/>", media_type="text/xml")


@router.post("/telegram/{integration_id}")
async def telegram_webhook(
    integration_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings), senders: SenderFactory = Depends(get_senders),
    llm: LLMProvider | None = Depends(get_llm_dep),
    embedder: EmbeddingProvider = Depends(get_embedder_dep),
    store: VectorStore = Depends(get_store_dep),
):
    if not telegram.verify_secret(settings.telegram_webhook_secret,
                                  request.headers.get("x-telegram-bot-api-secret-token")):
        raise HTTPException(401, "Invalid secret token")
    _check_size(request)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Invalid JSON") from None
    integ = _integration(db, telegram.PROVIDER, integration_id=integration_id)
    if integ is None:
        raise HTTPException(404, "Unknown integration")
    try:
        inbound = telegram.parse_update(payload, bot_id="telegram")
    except ChannelError as exc:
        raise HTTPException(400, str(exc)) from None
    if inbound is None:
        return {"status": "ignored"}
    hotel = db.get(Hotel, integ.hotel_id)
    result = await run_in_threadpool(
        process_inbound, db, settings=settings, hotel=hotel, inbound=inbound, senders=senders,
        llm=llm, embedder=embedder, store=store)
    return result
