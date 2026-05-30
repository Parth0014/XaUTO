import time

from app.database import get_db_client, init_indexes
from app.scheduler.jobs import start_scheduler, stop_scheduler


if __name__ == "__main__":
    init_indexes(get_db_client())
    start_scheduler()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        stop_scheduler()
