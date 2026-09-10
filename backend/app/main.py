import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState

from app.config import settings
from app.schemas import StartSessionMessage, StatusEvent, StopSessionMessage
from app.session import MeetingSession

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)

app = FastAPI(title="Meeting Assistant Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session: MeetingSession | None = None

    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if (text := message.get("text")) is not None:
                payload = json.loads(text)
                msg_type = payload.get("type")

                if msg_type == "start_session":
                    start_msg = StartSessionMessage.model_validate(payload)
                    try:
                        session = MeetingSession(
                            websocket=websocket,
                            stt_provider_name=start_msg.stt_provider,
                            audio_source=start_msg.audio_source,
                        )
                    except Exception as exc:
                        logger.exception("Failed to start STT provider: %s", start_msg.stt_provider)
                        await websocket.send_json(
                            StatusEvent(stage="error", message=str(exc)).model_dump()
                        )
                        continue
                    await session.start()
                elif msg_type == "stop_session":
                    StopSessionMessage.model_validate(payload)
                    if session is not None:
                        await session.stop()
                        session = None
            elif (data := message.get("bytes")) is not None:
                if session is not None:
                    await session.push_audio_chunk(data)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        if session is not None:
            await session.stop()
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close()


if __name__ == "__main__":
    import uvicorn

    # BACKEND_PORT を待受ポートの単一の正とするための開発用エントリポイント。
    # `uv run python -m app.main` で起動する(uvicornコマンドの --port 指定は不要)。
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=settings.backend_port,
        reload=settings.env == "development",
        log_level=settings.log_level.lower(),
    )
