import redis
import json
import threading
import time

QUEUE = "test_queue"

r = redis.Redis(
    host="localhost",
    port=6380,
    decode_responses=True,
    socket_keepalive=True,
    health_check_interval=30
)

# test connection
try:
    print("PING:", r.ping())
except Exception as e:
    print("Connection error:", e)
    exit(1)


def worker():
    print("Worker waiting for job...")
    while True:
        try:
            item = r.blpop(QUEUE, timeout=0)

            if item is None:
                continue

            queue_name, raw = item

            print("Received raw:", raw)

            try:
                job = json.loads(raw)
                print("Parsed job:", job)
            except:
                print("Not JSON:", raw)

        except Exception as e:
            print("BLPOP error:", e)
            time.sleep(2)


def producer():
    i = 0
    while True:
        job = {
            "id": i,
            "task": "test",
            "time": time.time()
        }

        r.rpush(QUEUE, json.dumps(job))
        print("Pushed job:", job)

        i += 1
        time.sleep(5)


threading.Thread(target=worker, daemon=True).start()
# threading.Thread(target=producer, daemon=True).start()

while True:
    time.sleep(1)