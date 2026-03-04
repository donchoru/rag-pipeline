"""스케줄러 진입점 — APScheduler로 파이프라인 자동 실행."""

import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

from config import SCHEDULE_CRON
from pipeline import run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = BlockingScheduler(timezone="Asia/Seoul")
scheduler.add_job(run_pipeline, "cron", **SCHEDULE_CRON, id="rag_pipeline")


def shutdown(signum, frame):
    logger.info("Shutting down scheduler…")
    scheduler.shutdown(wait=False)
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

if __name__ == "__main__":
    next_run = scheduler.get_job("rag_pipeline").next_run_time
    logger.info(f"RAG Pipeline 스케줄러 시작 — 다음 실행: {next_run}")
    logger.info(f"스케줄: {SCHEDULE_CRON}")
    scheduler.start()
